/**
 * Heartbeat adapter — calls native HubOS backend directly.
 *
 * Native routes (under `/api/config`):
 * - GET /api/config/heartbeat   → HeartbeatConfig (camelCase via `by_alias=true`)
 * - PUT /api/config/heartbeat   → HeartbeatConfig
 *
 * The backend models use `populate_by_name=true` with `activeHours` alias,
 * so the camelCase payload used by the UI is accepted natively without
 * any case translation.
 */

import { request } from "../request";
import type { HeartbeatConfig } from "../types/heartbeat";

export const heartbeatAdapter = {
  getHeartbeatConfig: () =>
    request<HeartbeatConfig>("/config/heartbeat"),

  updateHeartbeatConfig: (body: HeartbeatConfig) =>
    request<HeartbeatConfig>("/config/heartbeat", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
