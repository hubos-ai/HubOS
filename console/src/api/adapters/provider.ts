/**
 * Provider/Models adapter — calls native HubOS backend directly.
 *
 * Native routes (prefix `/models` under `/api`):
 * - GET    /api/models                                            → ProviderInfo[]
 * - PUT    /api/models/{provider_id}/config                        → ProviderInfo
 * - POST   /api/models/custom-providers                            → ProviderInfo
 * - DELETE /api/models/custom-providers/{provider_id}              → ProviderInfo[]
 * - POST   /api/models/{provider_id}/test                          → TestConnectionResponse
 * - POST   /api/models/{provider_id}/discover                      → DiscoverModelsResponse
 * - POST   /api/models/{provider_id}/models/test                   → TestConnectionResponse
 * - POST   /api/models/{provider_id}/models                        → ProviderInfo
 * - DELETE /api/models/{provider_id}/models/{model_id:path}        → ProviderInfo
 * - POST   /api/models/{provider_id}/models/{model_id:path}/probe-multimodal → ProbeMultimodalResponse
 * - GET    /api/models/active?scope=...&agent_id=...               → ActiveModelsInfo
 * - PUT    /api/models/active                                      → ActiveModelsInfo
 *
 * The backend returns the exact shapes the frontend types declare, so this
 * adapter is a thin pass-through plus query-string composition.
 */

import { request } from "../request";
import type {
  ProviderInfo,
  ProviderConfigRequest,
  ActiveModelsInfo,
  GetActiveModelsRequest,
  ModelSlotRequest,
  CreateCustomProviderRequest,
  AddModelRequest,
  TestConnectionResponse,
  TestProviderRequest,
  TestModelRequest,
  DiscoverModelsResponse,
  ProbeMultimodalResponse,
} from "../types";

function buildActiveQuery(params?: GetActiveModelsRequest): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      usp.append(k, String(v));
    }
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}

export const providerAdapter = {
  listProviders: () => request<ProviderInfo[]>("/models"),

  configureProvider: (providerId: string, body: ProviderConfigRequest) =>
    request<ProviderInfo>(`/models/${encodeURIComponent(providerId)}/config`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  getActiveModels: (params?: GetActiveModelsRequest) =>
    request<ActiveModelsInfo>(`/models/active${buildActiveQuery(params)}`),

  setActiveLlm: (body: ModelSlotRequest) =>
    request<ActiveModelsInfo>("/models/active", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  createCustomProvider: (body: CreateCustomProviderRequest) =>
    request<ProviderInfo>("/models/custom-providers", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteCustomProvider: (providerId: string) =>
    request<ProviderInfo[]>(
      `/models/custom-providers/${encodeURIComponent(providerId)}`,
      { method: "DELETE" },
    ),

  addModel: (providerId: string, body: AddModelRequest) =>
    request<ProviderInfo>(`/models/${encodeURIComponent(providerId)}/models`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  removeModel: (providerId: string, modelId: string) =>
    request<ProviderInfo>(
      `/models/${encodeURIComponent(providerId)}/models/${encodeURIComponent(
        modelId,
      )}`,
      { method: "DELETE" },
    ),

  testProviderConnection: (providerId: string, body?: TestProviderRequest) =>
    request<TestConnectionResponse>(
      `/models/${encodeURIComponent(providerId)}/test`,
      {
        method: "POST",
        body: JSON.stringify(body || {}),
      },
    ),

  testModelConnection: (providerId: string, body: TestModelRequest) =>
    request<TestConnectionResponse>(
      `/models/${encodeURIComponent(providerId)}/models/test`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),

  discoverModels: (providerId: string, body?: TestProviderRequest) =>
    request<DiscoverModelsResponse>(
      `/models/${encodeURIComponent(providerId)}/discover`,
      {
        method: "POST",
        body: JSON.stringify(body || {}),
      },
    ),

  probeMultimodal: (providerId: string, modelId: string) =>
    request<ProbeMultimodalResponse>(
      `/models/${encodeURIComponent(providerId)}/models/${encodeURIComponent(
        modelId,
      )}/probe-multimodal`,
      {
        method: "POST",
      },
    ),
};
