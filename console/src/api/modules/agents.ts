/**
 * Agents module — HubOS console agents API against XClaw backend
 *
 * All API calls are forwarded to the agentsAdapter which maps HubOS's
 * expected shapes to XClaw's actual backend shapes.
 *
 * Key differences handled by adapter:
 * - HubOS uses `id`; XClaw uses `name` as the primary key
 * - HubOS has `enabled` field; XClaw agents are always implicitly enabled
 * - XClaw reorder and toggle endpoints need special handling
 */

import { agentsAdapter } from "../adapters/agents";

// Re-export the adapter under the agentsApi name so existing imports work
export const agentsApi = agentsAdapter;
