/**
 * Tools adapter — calls native HubOS backend directly.
 *
 * Native routes (prefix `/tools` under `/api`):
 * - GET   /api/tools                                  → ToolInfo[]
 * - PATCH /api/tools/{tool_name}/toggle               → ToolInfo
 * - PATCH /api/tools/{tool_name}/async-execution      → ToolInfo
 *
 * Response shapes match `ToolInfo` directly (flat array, `async_execution`
 * field present), so this adapter is a thin pass-through.
 */

import { request } from "../request";
import type { ToolInfo } from "../modules/tools";

export interface ToolsAdapter {
  listTools: () => Promise<ToolInfo[]>;
  toggleTool: (toolName: string) => Promise<ToolInfo>;
  updateAsyncExecution: (
    toolName: string,
    asyncExecution: boolean,
  ) => Promise<{ name: string; async_execution: boolean }>;
}

export const toolsAdapter: ToolsAdapter = {
  listTools: () => request<ToolInfo[]>("/tools"),

  toggleTool: (toolName) =>
    request<ToolInfo>(
      `/tools/${encodeURIComponent(toolName)}/toggle`,
      { method: "PATCH" },
    ),

  updateAsyncExecution: (toolName, asyncExecution) =>
    request<ToolInfo>(
      `/tools/${encodeURIComponent(toolName)}/async-execution`,
      {
        method: "PATCH",
        body: JSON.stringify({ async_execution: asyncExecution }),
      },
    ).then((t) => ({ name: t.name, async_execution: t.async_execution })),
};
