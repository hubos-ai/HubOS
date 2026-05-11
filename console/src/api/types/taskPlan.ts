// -*- coding: utf-8 -*-
/** TypeScript types for the Task Plan API. */
export type PlanStatus =
  | "draft"
  | "running"
  | "waiting_user"
  | "done"
  | "failed"
  | "cancelled";

export type PlanStepStatus =
  | "pending"
  | "running"
  | "waiting_user"
  | "done"
  | "failed"
  | "cancelled";

export type PlanEventType =
  | "plan_created"
  | "plan_updated"
  | "step_added"
  | "step_updated"
  | "step_started"
  | "step_completed"
  | "step_failed"
  | "plan_cancelled";

export interface TaskPlanStep {
  step_id: string;
  title: string;
  description: string;
  status: PlanStepStatus;
  order: number;
  agent_id: string | null;
  tool_name: string | null;
  depends_on: string[];
  metadata: Record<string, unknown> | null;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  error: string | null;
}

export interface TaskPlan {
  plan_id: string;
  session_id: string;
  title: string;
  status: PlanStatus;
  steps: TaskPlanStep[];
  current_step_id: string | null;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  metadata: Record<string, unknown> | null;
}

export interface PlanListResponse {
  plans: TaskPlan[];
  count: number;
}

export interface PlanSSEEvent {
  type: PlanEventType;
  plan_id: string;
  data: Record<string, unknown>;
  timestamp: number;
}
