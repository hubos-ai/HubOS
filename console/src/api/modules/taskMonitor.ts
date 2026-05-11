// -*- coding: utf-8 -*-
/** Task Monitor API module. */
import { request } from "../request";
import type { Task, TaskListResponse } from "../types/taskMonitor";

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

export const taskMonitorApi = {
  listTasks: (params?: {
    status?: string;
    session_id?: string;
    tool_name?: string;
    limit?: number;
  }) =>
    request<TaskListResponse>(
      "/task-monitor/tasks" + (params ? `?${buildQuery(params)}` : ""),
    ),

  getTask: (taskId: string) =>
    request<Task>(`/task-monitor/tasks/${encodeURIComponent(taskId)}`),

  cancelTask: (taskId: string) =>
    request<{ task_id: string; status: string }>(
      `/task-monitor/tasks/${encodeURIComponent(taskId)}/cancel`,
      {
        method: "POST",
      },
    ),
};
