// -*- coding: utf-8 -*-
/** Task Plan API module. */
import { request } from "../request";
import type {
  TaskPlan,
  TaskPlanStep,
  PlanListResponse,
} from "../types/taskPlan";

function buildQuery(
  params?: Record<string, string | number | undefined>,
): string {
  if (!params) return "";
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    }
  }
  return parts.join("&");
}

export const taskPlanApi = {
  listPlans: (params?: {
    session_id?: string;
    status?: string;
    limit?: number;
  }) =>
    request<PlanListResponse>(
      "/task-plans" + (params ? `?${buildQuery(params)}` : ""),
    ),

  getPlan: (planId: string) =>
    request<TaskPlan>(`/task-plans/${encodeURIComponent(planId)}`),

  createPlan: (body: {
    session_id: string;
    title: string;
    steps?: Array<{
      title: string;
      description?: string;
      agent_id?: string | null;
      tool_name?: string | null;
      depends_on?: string[];
      metadata?: Record<string, unknown> | null;
    }>;
    metadata?: Record<string, unknown> | null;
  }) =>
    request<TaskPlan>("/task-plans", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  cancelPlan: (planId: string) =>
    request<TaskPlan>(`/task-plans/${encodeURIComponent(planId)}/cancel`, {
      method: "POST",
    }),

  startPlan: (planId: string) =>
    request<{ plan_id: string; started: boolean }>(
      `/task-plans/${encodeURIComponent(planId)}/start`,
      { method: "POST" },
    ),

  pausePlan: (planId: string) =>
    request<TaskPlan>(`/task-plans/${encodeURIComponent(planId)}/pause`, {
      method: "POST",
    }),

  resumePlan: (planId: string) =>
    request<TaskPlan>(`/task-plans/${encodeURIComponent(planId)}/resume`, {
      method: "POST",
    }),

  addStep: (
    planId: string,
    body: {
      title: string;
      description?: string;
      agent_id?: string | null;
      tool_name?: string | null;
      depends_on?: string[];
      metadata?: Record<string, unknown> | null;
      after_step_id?: string | null;
    },
  ) =>
    request<TaskPlanStep>(`/task-plans/${encodeURIComponent(planId)}/steps`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateStepStatus: (
    planId: string,
    stepId: string,
    body: {
      status: string;
      error?: string | null;
      metadata?: Record<string, unknown> | null;
    },
  ) =>
    request<TaskPlanStep>(
      `/task-plans/${encodeURIComponent(planId)}/steps/${encodeURIComponent(
        stepId,
      )}/status`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
};
