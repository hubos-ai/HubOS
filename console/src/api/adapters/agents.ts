/**
 * Agents adapter — calls native HubOS backend directly.
 *
 * Native routes (prefix `/agents` under `/api`):
 * - GET    /api/agents                                  → {agents: AgentSummary[]}
 * - POST   /api/agents                                  → AgentProfileRef
 * - GET    /api/agents/{id}                             → AgentProfileConfig
 * - PUT    /api/agents/{id}                             → AgentProfileConfig
 * - DELETE /api/agents/{id}                             → {success, agent_id}
 * - PUT    /api/agents/order                            → ReorderAgentsResponse
 * - PATCH  /api/agents/{id}/toggle                      → {id, enabled}
 * - GET    /api/agents/{id}/files                       → MdFileInfo[]
 * - GET    /api/agents/{id}/files/{filename}            → MdFileContent
 * - PUT    /api/agents/{id}/files/{filename}            → {written, filename}
 * - GET    /api/agents/{id}/memory-logs                 → MdFileInfo[]
 */

import { request } from "../request";
import type {
  AgentProfileConfig,
  CreateAgentRequest,
  AgentProfileRef,
  ReorderAgentsResponse,
  AgentSummary,
} from "../types/agents";
import type { MdFileInfo, MdFileContent } from "../types/workspace";

export const agentsAdapter = {
  listAgents: () => request<{ agents: AgentSummary[] }>("/agents"),

  getAgent: (agentId: string) =>
    request<AgentProfileConfig>(`/agents/${encodeURIComponent(agentId)}`),

  createAgent: (agent: CreateAgentRequest) =>
    request<AgentProfileRef>("/agents", {
      method: "POST",
      body: JSON.stringify(agent),
    }),

  updateAgent: (agentId: string, agent: AgentProfileConfig) =>
    request<AgentProfileConfig>(`/agents/${encodeURIComponent(agentId)}`, {
      method: "PUT",
      body: JSON.stringify(agent),
    }),

  deleteAgent: (agentId: string) =>
    request<{ success: boolean; agent_id: string }>(
      `/agents/${encodeURIComponent(agentId)}`,
      { method: "DELETE" },
    ),

  reorderAgents: (agentIds: string[]) =>
    request<ReorderAgentsResponse>("/agents/order", {
      method: "PUT",
      body: JSON.stringify({ agent_ids: agentIds }),
    }),

  toggleAgentEnabled: (agentId: string, enabled: boolean) =>
    request<{ id: string; enabled: boolean }>(
      `/agents/${encodeURIComponent(agentId)}/toggle`,
      {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      },
    ),

  listAgentFiles: (agentId: string) =>
    request<MdFileInfo[]>(`/agents/${encodeURIComponent(agentId)}/files`),

  readAgentFile: (agentId: string, filename: string) =>
    request<MdFileContent>(
      `/agents/${encodeURIComponent(agentId)}/files/${encodeURIComponent(
        filename,
      )}`,
    ),

  writeAgentFile: (agentId: string, filename: string, content: string) =>
    request<{ written: boolean; filename: string }>(
      `/agents/${encodeURIComponent(agentId)}/files/${encodeURIComponent(
        filename,
      )}`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
    ),

  listAgentMemory: (agentId: string) =>
    request<MdFileInfo[]>(`/agents/${encodeURIComponent(agentId)}/memory-logs`),
};
