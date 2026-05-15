/**
 * Admin sessions — cross-user L4 memory inspection.
 *
 * Backed by `/api/admin/sessions*` (see
 * src/hubos/app/routers/admin_sessions.py). All endpoints require the
 * caller to hold the `admin` role; a 403 indicates the current principal
 * cannot see this surface and the UI should hide the entry point.
 *
 * The request layer already attaches auth headers and centrally handles
 * 401 redirects; we only need to map HTTP errors into typed outcomes a
 * component can render without re-parsing strings.
 */

import { request } from "../request";

// ─── Wire types ────────────────────────────────────────────────────────────

export interface AdminSessionSummary {
  session_id: string;
  title: string;
  started: string;
  agent?: string;
  agent_id?: string;
  channel?: string;
  user_id?: string;
  tags?: string[];
  msg_count?: number;
  topics?: string[];
}

export interface AdminSessionListResponse {
  total: number;
  limit: number;
  offset: number;
  sessions: AdminSessionSummary[];
}

export interface AdminSessionMessage {
  role?: string;
  content?: unknown;
  timestamp?: string;
  [key: string]: unknown;
}

export interface AdminSessionDetailResponse {
  session_id: string;
  metadata: Record<string, unknown>;
  messages: AdminSessionMessage[];
  truncated: boolean;
  total_messages: number;
}

export interface AdminSessionMessagesResponse {
  session_id: string;
  offset: number;
  limit: number;
  total: number;
  messages: AdminSessionMessage[];
}

export interface AdminSessionListQuery {
  q?: string;
  startDate?: string;
  endDate?: string;
  userId?: string;
  agentId?: string;
  channel?: string;
  limit?: number;
  offset?: number;
}

// ─── Helpers ───────────────────────────────────────────────────────────────

function toQs(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

/**
 * Classify the Error thrown by request() into semantic buckets so
 * callers can distinguish "forbidden" (hide me) from "not found" (show
 * empty state) from transient / unknown failures.
 */
export type AdminSessionsErrorKind =
  | "forbidden"
  | "not_found"
  | "unauthorized"
  | "other";

export interface AdminSessionsError {
  kind: AdminSessionsErrorKind;
  message: string;
  raw: unknown;
}

export function classifyError(err: unknown): AdminSessionsError {
  const msg = err instanceof Error ? err.message : String(err);
  // request() throws "<message> - <body>" where body may contain the
  // structured 403/404 payload. We pattern-match defensively.
  if (/\b403\b|forbidden/i.test(msg)) {
    return { kind: "forbidden", message: msg, raw: err };
  }
  if (/\b404\b|not_found/i.test(msg)) {
    return { kind: "not_found", message: msg, raw: err };
  }
  if (/\b401\b|not authenticated/i.test(msg)) {
    return { kind: "unauthorized", message: msg, raw: err };
  }
  return { kind: "other", message: msg, raw: err };
}

// ─── API surface ───────────────────────────────────────────────────────────

export const adminSessionsApi = {
  list: (query: AdminSessionListQuery = {}) =>
    request<AdminSessionListResponse>(
      `/admin/sessions${toQs({
        q: query.q,
        start_date: query.startDate,
        end_date: query.endDate,
        user_id: query.userId,
        agent_id: query.agentId,
        channel: query.channel,
        limit: query.limit,
        offset: query.offset,
      })}`,
    ),

  get: (sessionId: string, lastN = 200) =>
    request<AdminSessionDetailResponse>(
      `/admin/sessions/${encodeURIComponent(sessionId)}${toQs({
        last_n: lastN,
      })}`,
    ),

  messages: (sessionId: string, offset = 0, limit = 200) =>
    request<AdminSessionMessagesResponse>(
      `/admin/sessions/${encodeURIComponent(sessionId)}/messages${toQs({
        offset,
        limit,
      })}`,
    ),

  delete: (sessionId: string) =>
    request<{ success?: boolean; deleted?: boolean }>(
      `/chats/${encodeURIComponent(sessionId)}`,
      {
        method: "DELETE",
      },
    ),

  /** Lightweight probe used by `useIsAdmin` — 200 ⇒ admin, 403 ⇒ not. */
  probe: () => request<AdminSessionListResponse>("/admin/sessions?limit=1"),
};
