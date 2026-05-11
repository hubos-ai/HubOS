// -*- coding: utf-8 -*-
/** Run Control API module — unified cancel, guidance, and run tree. */
import { request } from "../request";

export interface RunEntry {
  run_id: string;
  run_type: "chat" | "spawn" | "workflow" | "delegate" | "plan";
  session_id: string;
  status: "pending" | "running" | "waiting" | "done" | "failed" | "cancelled";
  created_at: number;
  monitor_task_id?: string;
  plan_id?: string;
  workflow_id?: string;
  chat_id?: string;
  parent_run_id?: string;
  child_run_ids?: string[];
  guidance_messages?: string[];
  cancellable: boolean;
  cancel_behavior: "real" | "mark_only";
  guided_from_run_id?: string;
  guidance_text?: string;
}

export interface RunListResponse {
  runs: RunEntry[];
  count: number;
}

export interface GuidanceResponse {
  run_id: string;
  guidance_ack: string;
  guidance_text: string;
  cancelled_run_id: string;
  cancelled: boolean;
}

export const runControlApi = {
  /** List runs. If sessionId is omitted, returns runs across sessions. */
  listRuns: (params?: { sessionId?: string; activeOnly?: boolean }) => {
    const search = new URLSearchParams();
    if (params?.sessionId) search.set("session_id", params.sessionId);
    if (params?.activeOnly !== undefined) {
      search.set("active_only", String(params.activeOnly));
    }
    const query = search.toString();
    return request<RunListResponse>(
      `/run-control/runs${query ? `?${query}` : ""}`,
    );
  },

  /** Get active runs for a session. */
  getActiveRuns: (sessionId: string) =>
    request<RunListResponse>(
      `/run-control/sessions/${encodeURIComponent(sessionId)}/active`,
    ),

  /** Get a single run's detail. */
  getRun: (runId: string) =>
    request<RunEntry>(`/run-control/runs/${encodeURIComponent(runId)}`),

  /** Get run tree (run + all descendants). */
  getRunTree: (runId: string) =>
    request<RunListResponse>(
      `/run-control/runs/${encodeURIComponent(runId)}/tree`,
    ),

  /** Cancel a specific run and its children. */
  cancelRun: (runId: string) =>
    request<{ run_id: string; cancelled: boolean }>(
      `/run-control/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" },
    ),

  /** Send guidance to a running run (cancels it, returns ack for restart). */
  guidance: (runId: string, text: string) =>
    request<GuidanceResponse>(
      `/run-control/runs/${encodeURIComponent(runId)}/guidance`,
      { method: "POST", body: JSON.stringify({ text }) },
    ),

  /** Cancel all active runs for a session. */
  cancelAll: (sessionId: string) =>
    request<{
      session_id: string;
      cancelled_count: number;
      cancelled_run_ids: string[];
    }>(`/run-control/sessions/${encodeURIComponent(sessionId)}/cancel-all`, {
      method: "POST",
    }),
};

/**
 * Find the best controllable run from a list of active runs.
 *
 * Priority:
 * 1. Root run (parent_run_id is empty) with status "running"
 * 2. Most recently created active run
 * 3. null if no active runs
 */
/** Statuses considered "active" for controllable-run selection. */
const _ACTIVE_STATUSES = new Set(["running", "pending", "waiting"]);

export function findControllableRun(runs: RunEntry[]): RunEntry | null {
  if (!runs || runs.length === 0) return null;

  const active = runs.filter((r) => _ACTIVE_STATUSES.has(r.status));
  if (active.length === 0) return null;

  // Prefer root runs (parent_run_id is empty/null)
  const roots = active.filter((r) => !r.parent_run_id);
  if (roots.length > 0) {
    // Most recent root
    roots.sort((a, b) => b.created_at - a.created_at);
    return roots[0];
  }

  // No root — pick most recent active run
  active.sort((a, b) => b.created_at - a.created_at);
  return active[0];
}
