// -*- coding: utf-8 -*-
/** TypeScript types for the Task Monitor API. */
export type TaskStatus =
  | "pending"
  | "running"
  | "waiting"
  | "done"
  | "failed"
  | "cancelled";

export type TaskEventType =
  | "task_created"
  | "task_updated"
  | "stage_started"
  | "stage_completed"
  | "log"
  | "error"
  | "task_done"
  | "task_failed"
  | "task_cancelled";

export interface TaskEvent {
  event_type: TaskEventType;
  message: string;
  timestamp: number;
  stage?: string;
  agent_id?: string;
  metadata?: Record<string, unknown>;
}

export interface Task {
  task_id: string;
  session_id: string;
  source: string;
  title: string;
  status: TaskStatus;
  created_at: number;
  updated_at: number;
  tool_name?: string;
  agent_id?: string;
  current_stage?: string;
  progress?: number;
  events: TaskEvent[];
  result_summary?: string;
  error?: string;
  metadata?: Record<string, unknown>;
  finished_at?: number;
}

export interface TaskListResponse {
  tasks: Task[];
  count: number;
}

export interface SSEEvent {
  type: TaskEventType;
  task_id: string;
  data: Record<string, unknown>;
  timestamp: number;
}
