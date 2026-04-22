/**
 * Heartbeat module — HubOS console heartbeat API against XClaw backend
 *
 * Path: /config/heartbeat → /api/heartbeat (adapter handles)
 * Shape: activeHours (camelCase) ↔ active_hours (snake_case)
 */

import { heartbeatAdapter } from "../adapters/heartbeat";

export const heartbeatApi = heartbeatAdapter;
