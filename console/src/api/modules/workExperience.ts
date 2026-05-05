/** Work Experience API module. */
import { request } from "@/api/request";

// Types for work experience cards
export interface WorkExperienceCard {
  experience_id: string;
  scope: string;
  trigger_keywords: string[];
  trigger_hint: string;
  title: string;
  what_happened: string;
  what_worked: string[];
  what_failed: string[];
  guidance: string;
  avoidance: string;
  // New work guidance fields
  usage_pattern_summary: string;
  recommended_tool_order: string[];
  recommended_workflow: string[];
  applicable_task_types: string[];
  success_rate_estimate: number;
  supersedes_experience_id: string | null;
  // Metadata
  confidence: number;
  source_task_id: string;
  source_session_id: string;
  source_trace_id: string;
  applicability_tags: string[];
  hit_count: number;
  effective_count: number;
  last_retrieved_at: string | null;
  last_used_at: string | null;
  disabled: boolean;
  // Legacy governance state
  status: string;
  // New maturity model
  experience_level: string;
  maturity_score: number;
  quality_score: number;
  created_at: string;
  updated_at: string;
}

export interface CardListResponse {
  cards: WorkExperienceCard[];
  total: number;
}

export interface CardSummary {
  experience_id: string;
  title: string;
  scope: string;
  status: string;
  experience_level: string;
  confidence: number;
  hit_count: number;
  effective_count: number;
  maturity_score: number;
  quality_score: number;
  trigger_hint: string;
}

export interface DuplicateDetectionResponse {
  reference_card_id: string;
  duplicates: CardSummary[];
  count: number;
}

export interface MergeRequest {
  source_id: string;
  target_id: string;
}

export interface MergeResponse {
  success: boolean;
  merged_into: string;
  archived: string;
  message: string;
}

export interface StatusTransitionResponse {
  success: boolean;
  card_id: string;
  new_status: string;
}

export interface LevelTransitionResponse {
  success: boolean;
  card_id: string;
  new_level: string;
}

export interface WorkExperienceStats {
  total_cards: number;
  by_status: Record<string, number>;
  by_level: Record<string, number>;
  by_scope: Record<string, number>;
  total_hits: number;
  total_effective_uses: number;
  avg_confidence: number;
  avg_quality_score: number;
  avg_maturity_score: number;
  top_scoring_card: CardSummary | null;
}

export interface QualityScoreBreakdown {
  experience_id: string;
  confidence: number;
  hit_count: number;
  effective_count: number;
  quality_score: number;
  formula: string;
}

export interface MaturityBreakdown {
  experience_id: string;
  experience_level: string;
  maturity_score: number;
  success_rate_estimate: number;
  effective_ratio: number;
  hit_count: number;
  effective_count: number;
  level_weight: number;
}

// Settings types — reflection model selection
export interface ProviderModelInfo {
  id: string;
  name: string;
}

export interface ProviderInfoForWE {
  provider_id: string;
  name: string;
  base_url: string;
  models: ProviderModelInfo[];
}

export interface WorkExperienceSettingsResponse {
  reflection_provider_id: string;
  reflection_model: string;
  available_providers: ProviderInfoForWE[];
}

export const workExperienceApi = {
  listCards: async (params?: {
    status?: string;
    level?: string;
    scope?: string;
    include_disabled?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<CardListResponse> => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.level) qs.set("level", params.level);
    if (params?.scope) qs.set("scope", params.scope);
    if (params?.include_disabled) qs.set("include_disabled", "true");
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const query = qs.toString();
    return request<CardListResponse>(
      `/work-experience/cards${query ? `?${query}` : ""}`,
    );
  },

  getCard: async (cardId: string): Promise<WorkExperienceCard> => {
    return request<WorkExperienceCard>(`/work-experience/cards/${cardId}`);
  },

  getQualityScore: async (cardId: string): Promise<QualityScoreBreakdown> => {
    return request<QualityScoreBreakdown>(
      `/work-experience/cards/${cardId}/quality-score`,
    );
  },

  getMaturity: async (cardId: string): Promise<MaturityBreakdown> => {
    return request<MaturityBreakdown>(
      `/work-experience/cards/${cardId}/maturity`,
    );
  },

  transitionStatus: async (
    cardId: string,
    status: string,
  ): Promise<StatusTransitionResponse> => {
    return request<StatusTransitionResponse>(
      `/work-experience/cards/${cardId}/status`,
      {
        method: "PATCH",
        body: JSON.stringify({ status }),
      },
    );
  },

  transitionLevel: async (
    cardId: string,
    level: string,
  ): Promise<LevelTransitionResponse> => {
    return request<LevelTransitionResponse>(
      `/work-experience/cards/${cardId}/level`,
      {
        method: "PATCH",
        body: JSON.stringify({ level }),
      },
    );
  },

  approveCard: async (cardId: string): Promise<StatusTransitionResponse> => {
    return request<StatusTransitionResponse>(
      `/work-experience/cards/${cardId}/approve`,
      { method: "POST" },
    );
  },

  rejectCard: async (cardId: string): Promise<StatusTransitionResponse> => {
    return request<StatusTransitionResponse>(
      `/work-experience/cards/${cardId}/reject`,
      { method: "POST" },
    );
  },

  archiveCard: async (cardId: string): Promise<StatusTransitionResponse> => {
    return request<StatusTransitionResponse>(
      `/work-experience/cards/${cardId}/archive`,
      { method: "POST" },
    );
  },

  reactivateCard: async (cardId: string): Promise<StatusTransitionResponse> => {
    return request<StatusTransitionResponse>(
      `/work-experience/cards/${cardId}/reactivate`,
      { method: "POST" },
    );
  },

  promoteCard: async (cardId: string): Promise<LevelTransitionResponse> => {
    return request<LevelTransitionResponse>(
      `/work-experience/cards/${cardId}/promote`,
      { method: "POST" },
    );
  },

  demoteCard: async (cardId: string): Promise<LevelTransitionResponse> => {
    return request<LevelTransitionResponse>(
      `/work-experience/cards/${cardId}/demote`,
      { method: "POST" },
    );
  },

  deprecateCard: async (cardId: string): Promise<LevelTransitionResponse> => {
    return request<LevelTransitionResponse>(
      `/work-experience/cards/${cardId}/deprecate`,
      { method: "POST" },
    );
  },

  findDuplicates: async (
    cardId: string,
    threshold?: number,
  ): Promise<DuplicateDetectionResponse> => {
    const qs = threshold !== undefined ? `?threshold=${threshold}` : "";
    return request<DuplicateDetectionResponse>(
      `/work-experience/cards/${cardId}/duplicates${qs}`,
    );
  },

  mergeCards: async (body: MergeRequest): Promise<MergeResponse> => {
    return request<MergeResponse>("/work-experience/merge", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getStats: async (params?: {
    status?: string;
    level?: string;
  }): Promise<WorkExperienceStats> => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.level) qs.set("level", params.level);
    const query = qs.toString();
    return request<WorkExperienceStats>(
      `/work-experience/stats${query ? `?${query}` : ""}`,
    );
  },

  listCandidates: async (params?: {
    limit?: number;
    offset?: number;
  }): Promise<CardListResponse> => {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const query = qs.toString();
    return request<CardListResponse>(
      `/work-experience/candidates${query ? `?${query}` : ""}`,
    );
  },

  listTopCards: async (params?: {
    status?: string;
    level?: string;
    top_k?: number;
    scope?: string;
  }): Promise<CardListResponse> => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.level) qs.set("level", params.level);
    if (params?.top_k) qs.set("top_k", String(params.top_k));
    if (params?.scope) qs.set("scope", params.scope);
    const query = qs.toString();
    return request<CardListResponse>(
      `/work-experience/top-cards${query ? `?${query}` : ""}`,
    );
  },

  getByLevel: async (): Promise<Record<string, number>> => {
    return request<Record<string, number>>(`/work-experience/by-level`);
  },

  // ---- Settings (reflection model selection) ----

  getSettings: async (): Promise<WorkExperienceSettingsResponse> => {
    return request<WorkExperienceSettingsResponse>("/work-experience/settings");
  },

  updateSettings: async (
    providerId: string,
    model: string,
  ): Promise<WorkExperienceSettingsResponse> => {
    return request<WorkExperienceSettingsResponse>(
      "/work-experience/settings",
      {
        method: "PUT",
        body: JSON.stringify({
          reflection_provider_id: providerId,
          reflection_model: model,
        }),
      },
    );
  },
};
