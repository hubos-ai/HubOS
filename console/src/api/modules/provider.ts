/**
 * Provider module — HubOS console provider/models API against XClaw backend
 *
 * Key fixes applied:
 * - listProviders(): /models → 500; redirected to /api/providers (works)
 * - getActiveModels(): /models/active → /api/providers/models/active (shape adapted)
 * - discoverModels: not supported → honest reject
 * - testModelConnection: not supported → honest reject
 * - probeMultimodal: POST → GET adapted
 */

import { providerAdapter } from "../adapters/provider";

export const providerApi = providerAdapter;
