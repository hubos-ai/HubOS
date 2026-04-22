/**
 * TokenUsage module — HubOS console token usage API against XClaw backend
 *
 * GET /token-usage → /api/token-usage
 * Shape: calls → call_count, no provider_id/model in stats (from key)
 */

import { tokenUsageAdapter } from "../adapters/tokenUsage";

export interface GetTokenUsageParams {
  start_date: string;
  end_date: string;
}

export const tokenUsageApi = {
  getTokenUsage: (params: GetTokenUsageParams) =>
    tokenUsageAdapter.getTokenUsage(params),
};
