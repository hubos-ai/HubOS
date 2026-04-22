export * from "./types";

export { request } from "./request";

export { getApiUrl, getApiToken } from "./config";

import { rootApi } from "./modules/root";
import { channelApi } from "./modules/channel";
import { heartbeatApi } from "./modules/heartbeat";
import { cronJobAdapter } from "./adapters/cronjob";
import { sessionApi } from "./modules/chat";
import { chatAdapter } from "./adapters/chat";
import { envApi } from "./modules/env";
import { providerApi } from "./modules/provider";
import { skillApi } from "./modules/skill";
import { agentApi } from "./modules/agent";
import { agentsApi } from "./modules/agents";
import { workspaceApi } from "./modules/workspace";
import { localModelApi } from "./modules/localModel";
import { mcpAdapter } from "./adapters/mcp";
import { tokenUsageApi } from "./modules/tokenUsage";
import { toolsAdapter } from "./adapters/tools";
import { securityApi } from "./modules/security";
import { userTimezoneApi } from "./modules/userTimezone";
import { languageApi } from "./modules/language";
import { skillAdapter } from "./adapters/skill";
import { workExperienceApi } from "./modules/workExperience";

export const api = {
  // Root
  ...rootApi,

  // Channels
  ...channelApi,

  // Heartbeat
  ...heartbeatApi,

  // Cron Jobs
  ...cronJobAdapter,

  // Sessions（Legacy aliases）
  ...sessionApi,

  // Environment Variables
  ...envApi,

  // Providers
  ...providerApi,

  // Agent
  ...agentApi,

  // Skills — base from skillApi
  ...skillApi,

  // Workspace
  ...workspaceApi,

  // Local Models
  ...localModelApi,

  // MCP Clients
  ...mcpAdapter,

  // Token Usage
  ...tokenUsageApi,

  // Tools
  ...toolsAdapter,

  // Security (must come BEFORE skill-specific overrides so overrides win)
  ...securityApi,

  // Skills — explicit overrides after securityApi spread
  // These take precedence because they appear after ...securityApi
  listSkills: skillAdapter.listSkills,
  getSkill: skillAdapter.getSkill,
  enableSkill: skillAdapter.enableSkill,
  disableSkill: skillAdapter.disableSkill,
  batchEnableSkills: skillAdapter.batchEnableSkills,
  batchDisableSkills: skillAdapter.batchDisableSkills,
  batchDeleteSkills: skillAdapter.batchDeleteSkills,
  deleteSkill: skillAdapter.deleteSkill,
  getBlockedHistory: skillAdapter.getBlockedHistory,
  streamOptimizeSkill: skillAdapter.streamOptimizeSkill,
  createSkill: skillAdapter.createSkill,
  saveSkill: skillAdapter.saveSkill,
  updateSkillChannels: skillAdapter.updateSkillChannels,
  getSkillConfig: skillAdapter.getSkillConfig,
  updateSkillConfig: skillAdapter.updateSkillConfig,
  deleteSkillConfig: skillAdapter.deleteSkillConfig,
  refreshSkills: skillAdapter.refreshSkills,
  uploadSkill: skillAdapter.uploadSkill,
  startHubSkillInstall: skillAdapter.startHubSkillInstall,
  getHubSkillInstallStatus: skillAdapter.getHubSkillInstallStatus,
  cancelHubSkillInstall: skillAdapter.cancelHubSkillInstall,
  listSkillPoolSkills: skillAdapter.listSkillPoolSkills,
  refreshSkillPool: skillAdapter.refreshSkillPool,
  searchHubSkills: skillAdapter.searchHubSkills,
  listPoolBuiltinSources: skillAdapter.listPoolBuiltinSources,
  importSelectedPoolBuiltins: skillAdapter.importSelectedPoolBuiltins,
  updatePoolBuiltin: skillAdapter.updatePoolBuiltin,
  deleteSkillPoolSkill: skillAdapter.deleteSkillPoolSkill,
  uploadWorkspaceSkillToPool: skillAdapter.uploadWorkspaceSkillToPool,
  downloadSkillPoolSkill: skillAdapter.downloadSkillPoolSkill,
  listSkillWorkspaces: skillAdapter.listSkillWorkspaces,
  batchDeletePoolSkills: skillAdapter.batchDeletePoolSkills,
  importPoolSkillFromHub: skillAdapter.importPoolSkillFromHub,
  uploadSkillPoolZip: skillAdapter.uploadSkillPoolZip,
  saveSkillPoolSkill: skillAdapter.saveSkillPoolSkill,
  createSkillPoolSkill: skillAdapter.createSkillPoolSkill,
  // Note: getBlockedHistory from skillAdapter overrides securityApi version
  // (different XClaw endpoints: /api/skills/scan/blocked-history vs /config/security/skill-scanner/blocked-history)
  // getSkillScanner is NOT overridden — securityApi version is used

  // Chats — explicit overrides after all module spreads
  // These handle {chats: [...]} unwrapping and batch-delete {chat_ids: [...]} body transform
  // Must come after ...workspaceApi so chat uploadFile wins over workspace uploadFile
  listChats: chatAdapter.listChats,
  createChat: chatAdapter.createChat,
  getChat: chatAdapter.getChat,
  updateChat: chatAdapter.updateChat,
  deleteChat: chatAdapter.deleteChat,
  batchDeleteChats: chatAdapter.batchDeleteChats,
  stopChat: chatAdapter.stopChat,
  uploadFile: chatAdapter.uploadFile,
  filePreviewUrl: chatAdapter.filePreviewUrl,

  // User Timezone
  ...userTimezoneApi,

  // Language
  ...languageApi,

  // Work Experience
  ...workExperienceApi,
};

export default api;

// Export individual APIs for direct access
export { agentsApi };
