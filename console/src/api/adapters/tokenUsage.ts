/**
 * TokenUsage adapter — calls native HubOS backend directly.
 *
 * Native route:
 * - GET /api/token-usage?start_date=...&end_date=...&model=...&provider=...
 *         → TokenUsageSummary
 *
 * The backend TokenUsageSummary already uses `call_count` on each stats entry
 * (same shape the frontend type declares), so this adapter is a thin
 * query-string pass-through.
 */

import { request } from "../request";
import type { TokenUsageSummary } from "../types/tokenUsage";

export interface GetTokenUsageParams {
  start_date: string;
  end_date: string;
  model?: string;
  provider?: string;
}

function buildQuery(params: GetTokenUsageParams): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      usp.append(k, String(v));
    }
  }
  return usp.toString() ? `?${usp.toString()}` : "";
}

export const tokenUsageAdapter = {
  getTokenUsage: (params: GetTokenUsageParams) =>
    request<TokenUsageSummary>(`/token-usage${buildQuery(params)}`),
};
