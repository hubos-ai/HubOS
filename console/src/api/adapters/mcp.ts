/**
 * MCP adapter — calls native HubOS backend directly.
 *
 * Native routes (prefix `/mcp` under `/api`):
 * - GET    /api/mcp                       → MCPClientInfo[]
 * - GET    /api/mcp/{client_key}          → MCPClientInfo
 * - POST   /api/mcp                       → MCPClientInfo
 *            body: {client_key, client: MCPClientCreateRequest}
 * - PUT    /api/mcp/{client_key}          → MCPClientInfo
 *            body: MCPClientUpdateRequest (partial)
 * - PATCH  /api/mcp/{client_key}/toggle   → MCPClientInfo
 * - DELETE /api/mcp/{client_key}          → {message}
 *
 * The backend response shape already matches the frontend `MCPClientInfo`
 * type, so this adapter is a thin pass-through with no translation.
 */

import { request } from "../request";
import type { MCPClientInfo } from "../types/mcp";

export interface MCPAdapter {
  listMCPClients: () => Promise<MCPClientInfo[]>;
  getMCPClient: (clientKey: string) => Promise<MCPClientInfo>;
  createMCPClient: (body: {
    client_key: string;
    client: Partial<MCPClientInfo>;
  }) => Promise<MCPClientInfo>;
  updateMCPClient: (
    clientKey: string,
    body: Partial<MCPClientInfo>,
  ) => Promise<MCPClientInfo>;
  toggleMCPClient: (clientKey: string) => Promise<MCPClientInfo>;
  deleteMCPClient: (clientKey: string) => Promise<{ message: string }>;
}

export const mcpAdapter: MCPAdapter = {
  listMCPClients: () => request<MCPClientInfo[]>("/mcp"),

  getMCPClient: (clientKey: string) =>
    request<MCPClientInfo>(`/mcp/${encodeURIComponent(clientKey)}`),

  createMCPClient: (body) =>
    request<MCPClientInfo>("/mcp", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateMCPClient: (clientKey: string, body: Partial<MCPClientInfo>) =>
    request<MCPClientInfo>(`/mcp/${encodeURIComponent(clientKey)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  toggleMCPClient: (clientKey: string) =>
    request<MCPClientInfo>(`/mcp/${encodeURIComponent(clientKey)}/toggle`, {
      method: "PATCH",
    }),

  deleteMCPClient: (clientKey: string) =>
    request<{ message: string }>(`/mcp/${encodeURIComponent(clientKey)}`, {
      method: "DELETE",
    }),
};
