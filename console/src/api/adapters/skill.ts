/**
 * Skills adapter — calls native HubOS backend directly.
 *
 * Native routes (prefix `/skills` under `/api`, plus security scanner under
 * `/config/security/skill-scanner`). The backend returns the exact shapes the
 * frontend types declare, so this adapter is a thin pass-through.
 */

import { request } from "../request";
import { getApiUrl, getApiToken } from "../config";
import type {
  HubInstallTaskResponse,
  PoolSkillSpec,
  BuiltinImportSpec,
  WorkspaceSkillSummary,
} from "../types/skill";
import type { SkillSpec } from "../types/skill";
import type { BlockedSkillRecord } from "../modules/security";

async function streamOptimizeSkillImpl(
  content: string,
  onChunk: (text: string) => void,
  signal: AbortSignal,
  language: string = "en",
): Promise<void> {
  const url = getApiUrl("/skills/ai/optimize/stream");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getApiToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({ content, language }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No reader available");

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");

      for (let i = 0; i < lines.length - 1; i++) {
        const line = lines[i].trim();
        if (line.startsWith("data: ")) {
          const data = line.slice(6);
          try {
            const parsed = JSON.parse(data);
            if (parsed.text) {
              onChunk(parsed.text);
            } else if (parsed.error) {
              throw new Error(parsed.error);
            } else if (parsed.done) {
              return;
            }
          } catch {
            // ignore malformed chunks
          }
        }
      }

      buffer = lines[lines.length - 1];
    }
  } finally {
    reader.releaseLock();
  }
}

export interface SkillAdapter {
  listSkills: (agentId?: string) => Promise<SkillSpec[]>;
  getSkill: (skillName: string) => Promise<SkillSpec>;
  enableSkill: (skillName: string) => Promise<SkillSpec>;
  disableSkill: (skillName: string) => Promise<SkillSpec>;
  batchEnableSkills: (skillNames: string[]) => Promise<void>;
  batchDisableSkills: (skillNames: string[]) => Promise<void>;
  batchDeleteSkills: (skillNames: string[]) => Promise<{
    results: Record<string, { success: boolean; reason?: string }>;
  }>;
  deleteSkill: (skillName: string) => Promise<{ deleted: boolean }>;
  getBlockedHistory: () => Promise<BlockedSkillRecord[]>;
  streamOptimizeSkill: (
    content: string,
    onChunk: (text: string) => void,
    signal: AbortSignal,
    language?: string,
  ) => Promise<void>;
  createSkill: (
    name: string,
    content: string,
    config?: Record<string, unknown>,
    enable?: boolean,
  ) => Promise<{ created: boolean; name: string }>;
  saveSkill: (payload: {
    name: string;
    content: string;
    source_name?: string;
    config?: Record<string, unknown>;
  }) => Promise<{
    success: boolean;
    mode: "edit" | "rename" | "noop";
    name: string;
  }>;
  updateSkillChannels: (
    skillName: string,
    channels: string[],
  ) => Promise<{ updated: boolean; channels: string[] }>;
  getSkillConfig: (
    skillName: string,
  ) => Promise<{ config: Record<string, unknown> }>;
  updateSkillConfig: (
    skillName: string,
    config: Record<string, unknown>,
  ) => Promise<{ updated: boolean }>;
  deleteSkillConfig: (skillName: string) => Promise<{ cleared: boolean }>;
  refreshSkills: (agentId?: string) => Promise<SkillSpec[]>;
  uploadSkill: (
    file: File,
    options?: {
      enable?: boolean;
      overwrite?: boolean;
      target_name?: string;
      rename_map?: Record<string, string>;
    },
  ) => Promise<{
    imported: string[];
    count: number;
    enabled: boolean;
    conflicts?: Array<{
      reason: string;
      skill_name: string;
      suggested_name: string;
    }>;
  }>;
  startHubSkillInstall: (payload: {
    bundle_url: string;
    version?: string;
    enable?: boolean;
    overwrite?: boolean;
    target_name?: string;
  }) => Promise<HubInstallTaskResponse>;
  getHubSkillInstallStatus: (taskId: string) => Promise<HubInstallTaskResponse>;
  cancelHubSkillInstall: (
    taskId: string,
  ) => Promise<{ task_id: string; status: string }>;
  listSkillPoolSkills: () => Promise<PoolSkillSpec[]>;
  refreshSkillPool: () => Promise<PoolSkillSpec[]>;
  searchHubSkills: (q: string, limit?: number) => Promise<unknown>;
  listPoolBuiltinSources: () => Promise<BuiltinImportSpec[]>;
  importSelectedPoolBuiltins: (payload: {
    skill_names: string[];
    overwrite_conflicts?: boolean;
  }) => Promise<{
    imported: string[];
    updated: string[];
    unchanged: string[];
    conflicts: Array<{
      skill_name: string;
      source_version_text?: string;
      current_version_text?: string;
      current_source?: string;
    }>;
  }>;
  updatePoolBuiltin: (skillName: string) => Promise<unknown>;
  deleteSkillPoolSkill: (skillName: string) => Promise<{ deleted: boolean }>;
  uploadWorkspaceSkillToPool: (payload: {
    workspace_id: string;
    skill_name: string;
    new_name?: string;
    overwrite?: boolean;
  }) => Promise<{ success: boolean; name: string }>;
  downloadSkillPoolSkill: (payload: {
    skill_name: string;
    targets: Array<{ workspace_id: string; target_name?: string }>;
    all_workspaces?: boolean;
    overwrite?: boolean;
  }) => Promise<{
    downloaded: Array<{
      workspace_id: string;
      workspace_name?: string;
      name: string;
    }>;
    conflicts?: Array<{
      reason?: string;
      workspace_id?: string;
      workspace_name?: string;
      suggested_name?: string;
    }>;
  }>;
  getSkillScanner: () => Promise<unknown>;
  listSkillWorkspaces: () => Promise<WorkspaceSkillSummary[]>;
  batchDeletePoolSkills: (skillNames: string[]) => Promise<{
    results: Record<string, { success: boolean; reason?: string }>;
  }>;
  importPoolSkillFromHub: (payload: {
    bundle_url: string;
    version?: string;
    overwrite?: boolean;
    target_name?: string;
  }) => Promise<{
    installed: boolean;
    name: string;
    enabled: boolean;
    source_url: string;
  }>;
  uploadSkillPoolZip: (
    file: File,
    options?: {
      overwrite?: boolean;
      target_name?: string;
      rename_map?: Record<string, string>;
    },
  ) => Promise<{
    imported: string[];
    count: number;
    conflicts?: Array<{
      reason: string;
      skill_name: string;
      suggested_name: string;
    }>;
  }>;
  saveSkillPoolSkill: (payload: {
    name: string;
    content: string;
    source_name?: string;
    config?: Record<string, unknown>;
  }) => Promise<{
    success: boolean;
    mode: "edit" | "rename" | "noop";
    name: string;
  }>;
  createSkillPoolSkill: (payload: {
    name: string;
    content: string;
    config?: Record<string, unknown>;
  }) => Promise<{ created: boolean; name: string }>;
}

function buildUploadHeaders(): Record<string, string> {
  const token = getApiToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function postMultipart<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(getApiUrl(path), {
    method: "POST",
    headers: buildUploadHeaders(),
    body: form,
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

export const skillAdapter: SkillAdapter = {
  listSkills: () => request<SkillSpec[]>("/skills"),

  getSkill: async (skillName: string) => {
    const list = await request<SkillSpec[]>("/skills");
    const found = list.find((s) => s.name === skillName);
    if (!found) {
      return Promise.reject(new Error(`Skill "${skillName}" not found.`));
    }
    return found;
  },

  enableSkill: (skillName) =>
    request<SkillSpec>(`/skills/${encodeURIComponent(skillName)}/enable`, {
      method: "POST",
    }),

  disableSkill: (skillName) =>
    request<SkillSpec>(`/skills/${encodeURIComponent(skillName)}/disable`, {
      method: "POST",
    }),

  batchEnableSkills: async (skillNames) => {
    await request("/skills/batch-enable", {
      method: "POST",
      body: JSON.stringify(skillNames),
    });
  },

  batchDisableSkills: async (skillNames) => {
    await request("/skills/batch-disable", {
      method: "POST",
      body: JSON.stringify(skillNames),
    });
  },

  batchDeleteSkills: (skillNames) =>
    request<{ results: Record<string, { success: boolean; reason?: string }> }>(
      "/skills/batch-delete",
      {
        method: "POST",
        body: JSON.stringify(skillNames),
      },
    ),

  deleteSkill: (skillName) =>
    request<{ deleted: boolean }>(`/skills/${encodeURIComponent(skillName)}`, {
      method: "DELETE",
    }),

  getBlockedHistory: () =>
    request<BlockedSkillRecord[]>(
      "/config/security/skill-scanner/blocked-history",
    ),

  streamOptimizeSkill: streamOptimizeSkillImpl,

  createSkill: (name, content, config, enable) =>
    request<{ created: boolean; name: string }>("/skills", {
      method: "POST",
      body: JSON.stringify({ name, content, config, enable }),
    }),

  saveSkill: (payload) =>
    request<{
      success: boolean;
      mode: "edit" | "rename" | "noop";
      name: string;
    }>("/skills/save", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  updateSkillChannels: (skillName, channels) =>
    request<{ updated: boolean; channels: string[] }>(
      `/skills/${encodeURIComponent(skillName)}/channels`,
      {
        method: "PUT",
        body: JSON.stringify(channels),
      },
    ),

  getSkillConfig: (skillName) =>
    request<{ config: Record<string, unknown> }>(
      `/skills/${encodeURIComponent(skillName)}/config`,
    ),

  updateSkillConfig: (skillName, config) =>
    request<{ updated: boolean }>(
      `/skills/${encodeURIComponent(skillName)}/config`,
      {
        method: "PUT",
        body: JSON.stringify({ config }),
      },
    ),

  deleteSkillConfig: (skillName) =>
    request<{ cleared: boolean }>(
      `/skills/${encodeURIComponent(skillName)}/config`,
      { method: "DELETE" },
    ),

  refreshSkills: () =>
    request<SkillSpec[]>("/skills/refresh", { method: "POST" }),

  uploadSkill: async (file, options = {}) => {
    const form = new FormData();
    form.append("file", file);
    if (options.enable !== undefined)
      form.append("enable", String(options.enable));
    if (options.overwrite !== undefined)
      form.append("overwrite", String(options.overwrite));
    if (options.target_name) form.append("target_name", options.target_name);
    if (options.rename_map)
      form.append("rename_map", JSON.stringify(options.rename_map));
    return postMultipart("/skills/upload", form);
  },

  startHubSkillInstall: (payload) =>
    request<HubInstallTaskResponse>("/skills/hub/install/start", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getHubSkillInstallStatus: (taskId) =>
    request<HubInstallTaskResponse>(
      `/skills/hub/install/status/${encodeURIComponent(taskId)}`,
    ),

  cancelHubSkillInstall: (taskId) =>
    request<{ task_id: string; status: string }>(
      `/skills/hub/install/cancel/${encodeURIComponent(taskId)}`,
      { method: "POST" },
    ),

  listSkillPoolSkills: () => request<PoolSkillSpec[]>("/skills/pool"),

  refreshSkillPool: () =>
    request<PoolSkillSpec[]>("/skills/pool/refresh", { method: "POST" }),

  searchHubSkills: (q, limit) => {
    const usp = new URLSearchParams({ q });
    if (limit !== undefined) usp.set("limit", String(limit));
    return request<unknown>(`/skills/hub/search?${usp.toString()}`);
  },

  listPoolBuiltinSources: () =>
    request<BuiltinImportSpec[]>("/skills/pool/builtin-sources"),

  importSelectedPoolBuiltins: (payload) =>
    request<{
      imported: string[];
      updated: string[];
      unchanged: string[];
      conflicts: Array<{
        skill_name: string;
        source_version_text?: string;
        current_version_text?: string;
        current_source?: string;
      }>;
    }>("/skills/pool/import-builtin", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updatePoolBuiltin: (skillName) =>
    request<unknown>(
      `/skills/pool/${encodeURIComponent(skillName)}/update-builtin`,
      { method: "POST" },
    ),

  deleteSkillPoolSkill: (skillName) =>
    request<{ deleted: boolean }>(
      `/skills/pool/${encodeURIComponent(skillName)}`,
      { method: "DELETE" },
    ),

  uploadWorkspaceSkillToPool: (payload) =>
    request<{ success: boolean; name: string }>("/skills/pool/upload", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  downloadSkillPoolSkill: (payload) =>
    request<{
      downloaded: Array<{
        workspace_id: string;
        workspace_name?: string;
        name: string;
      }>;
      conflicts?: Array<{
        reason?: string;
        workspace_id?: string;
        workspace_name?: string;
        suggested_name?: string;
      }>;
    }>("/skills/pool/download", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSkillScanner: () => request<unknown>("/config/security/skill-scanner"),

  listSkillWorkspaces: () =>
    request<WorkspaceSkillSummary[]>("/skills/workspaces"),

  batchDeletePoolSkills: (skillNames) =>
    request<{ results: Record<string, { success: boolean; reason?: string }> }>(
      "/skills/pool/batch-delete",
      {
        method: "POST",
        body: JSON.stringify(skillNames),
      },
    ),

  importPoolSkillFromHub: (payload) =>
    request<{
      installed: boolean;
      name: string;
      enabled: boolean;
      source_url: string;
    }>("/skills/pool/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  uploadSkillPoolZip: async (file, options = {}) => {
    const form = new FormData();
    form.append("file", file);
    if (options.overwrite !== undefined)
      form.append("overwrite", String(options.overwrite));
    if (options.target_name) form.append("target_name", options.target_name);
    if (options.rename_map)
      form.append("rename_map", JSON.stringify(options.rename_map));
    return postMultipart("/skills/pool/upload-zip", form);
  },

  saveSkillPoolSkill: (payload) =>
    request<{
      success: boolean;
      mode: "edit" | "rename" | "noop";
      name: string;
    }>("/skills/pool/save", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  createSkillPoolSkill: (payload) =>
    request<{ created: boolean; name: string }>("/skills/pool/create", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
