/** useWorkExperience hook — state + API callbacks for Work Experience page. */
import { useState, useCallback, useEffect } from "react";
import { message } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import { workExperienceApi } from "@/api/modules/workExperience";
import type {
  WorkExperienceCard,
  WorkExperienceStats,
  DuplicateDetectionResponse,
  MergeRequest,
} from "@/api/modules/workExperience";

export type StatusFilter =
  | "all"
  | "candidate"
  | "approved"
  | "rejected"
  | "archived";
export type LevelFilter = "all" | "new" | "observed" | "mature" | "deprecated";
export type ScopeFilter = "all" | "global" | "user" | "project" | "session";

interface ListParams {
  status?: string;
  level?: string;
  scope?: string;
  include_disabled?: boolean;
  limit?: number;
  offset?: number;
}

export function useWorkExperience() {
  const [messageApi] = message.useMessage();
  const { t } = useTranslation();

  // Cards list state
  const [cards, setCards] = useState<WorkExperienceCard[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // Stats
  const [stats, setStats] = useState<WorkExperienceStats | null>(null);
  const [topCards, setTopCards] = useState<WorkExperienceCard[]>([]);

  // Detail drawer
  const [selectedCard, setSelectedCard] = useState<WorkExperienceCard | null>(
    null,
  );
  const [drawerLoading, setDrawerLoading] = useState(false);

  // Duplicates
  const [duplicates, setDuplicates] =
    useState<DuplicateDetectionResponse | null>(null);
  const [duplicatesLoading, setDuplicatesLoading] = useState(false);

  // Merge
  const [mergeLoading, setMergeLoading] = useState(false);

  // Filter state
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("all");
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>("all");
  const [includeDisabled, setIncludeDisabled] = useState(false);
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 20;

  // Fetch cards with current filter state (avoids stale closure in useEffect)
  const fetchWithFilters = useCallback(
    async (pageNum: number) => {
      setLoading(true);
      try {
        const params: ListParams = {
          limit: PAGE_SIZE,
          offset: (pageNum - 1) * PAGE_SIZE,
          include_disabled: includeDisabled,
        };
        if (statusFilter !== "all") params.status = statusFilter;
        if (levelFilter !== "all") params.level = levelFilter;
        if (scopeFilter !== "all") params.scope = scopeFilter;
        const resp = await workExperienceApi.listCards(params);
        setCards(resp.cards);
        setTotal(resp.total);
        setPage(pageNum);
      } catch (err) {
        console.error("Failed to load work experience cards:", err);
        messageApi.error(t("workExperience.loadCardsFailed"));
      } finally {
        setLoading(false);
      }
    },
    [includeDisabled, levelFilter, messageApi, scopeFilter, statusFilter, t],
  );

  const fetchStats = useCallback(async () => {
    try {
      const [statsResp, topResp] = await Promise.all([
        workExperienceApi.getStats(),
        workExperienceApi.listTopCards({ top_k: 5 }),
      ]);
      setStats(statsResp);
      setTopCards(topResp.cards);
    } catch (err) {
      console.error("Failed to load stats:", err);
    }
  }, []);

  // Auto-refetch when filters change — resets to page 1
  useEffect(() => {
    fetchWithFilters(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, levelFilter, scopeFilter, includeDisabled]);

  const fetchCardDetail = useCallback(
    async (cardId: string) => {
      setDrawerLoading(true);
      setSelectedCard(null);
      setDuplicates(null);
      try {
        const card = await workExperienceApi.getCard(cardId);
        setSelectedCard(card);
      } catch (err) {
        console.error("Failed to load card detail:", err);
        messageApi.error(t("workExperience.loadCardDetailsFailed"));
      } finally {
        setDrawerLoading(false);
      }
    },
    [messageApi, t],
  );

  const fetchDuplicates = useCallback(
    async (cardId: string, threshold: number = 0.5) => {
      setDuplicatesLoading(true);
      try {
        const resp = await workExperienceApi.findDuplicates(cardId, threshold);
        setDuplicates(resp);
      } catch (err) {
        console.error("Failed to find duplicates:", err);
        messageApi.error(t("workExperience.findDuplicatesFailed"));
      } finally {
        setDuplicatesLoading(false);
      }
    },
    [messageApi, t],
  );

  const doStatusTransition = useCallback(
    async (
      cardId: string,
      action: "approve" | "reject" | "archive" | "reactivate",
    ) => {
      const method =
        action === "approve"
          ? workExperienceApi.approveCard
          : action === "reject"
          ? workExperienceApi.rejectCard
          : action === "archive"
          ? workExperienceApi.archiveCard
          : workExperienceApi.reactivateCard;
      await method(cardId);
      await fetchWithFilters(page);
      await fetchStats();
      // Refresh selected card if it's the one we just transitioned
      if (selectedCard?.experience_id === cardId) {
        const updated = await workExperienceApi.getCard(cardId);
        setSelectedCard(updated);
      }
    },
    [fetchStats, fetchWithFilters, page, selectedCard],
  );

  const doLevelTransition = useCallback(
    async (cardId: string, action: "promote" | "demote" | "deprecate") => {
      const method =
        action === "promote"
          ? workExperienceApi.promoteCard
          : action === "demote"
          ? workExperienceApi.demoteCard
          : workExperienceApi.deprecateCard;
      await method(cardId);
      await fetchWithFilters(page);
      await fetchStats();
      if (selectedCard?.experience_id === cardId) {
        const updated = await workExperienceApi.getCard(cardId);
        setSelectedCard(updated);
      }
    },
    [fetchStats, fetchWithFilters, page, selectedCard],
  );

  const doMerge = useCallback(
    async (sourceId: string, targetId: string) => {
      setMergeLoading(true);
      try {
        const body: MergeRequest = { source_id: sourceId, target_id: targetId };
        await workExperienceApi.mergeCards(body);
        messageApi.success(t("workExperience.mergeSuccess"));
        setDuplicates(null);
        await fetchWithFilters(page);
        await fetchStats();
        if (selectedCard) {
          const updated = await workExperienceApi.getCard(
            selectedCard.experience_id,
          );
          setSelectedCard(updated);
        }
      } catch (err) {
        console.error("Failed to merge cards:", err);
        messageApi.error(t("workExperience.mergeFailed"));
        throw err;
      } finally {
        setMergeLoading(false);
      }
    },
    [fetchStats, fetchWithFilters, messageApi, page, selectedCard, t],
  );

  const handleFilterChange = useCallback(
    (
      newStatus: StatusFilter,
      newLevel: LevelFilter,
      newScope: ScopeFilter,
      disabled: boolean,
    ) => {
      // Update state — useEffect watches these and calls fetchWithFilters(1)
      setStatusFilter(newStatus);
      setLevelFilter(newLevel);
      setScopeFilter(newScope);
      setIncludeDisabled(disabled);
    },
    [],
  );

  return {
    // Cards list
    cards,
    total,
    loading,
    page,
    pageSize: PAGE_SIZE,
    fetchCards: fetchWithFilters,
    // Stats
    stats,
    topCards,
    fetchStats,
    // Filters
    statusFilter,
    levelFilter,
    scopeFilter,
    includeDisabled,
    handleFilterChange,
    // Detail drawer
    selectedCard,
    drawerLoading,
    fetchCardDetail,
    setSelectedCard,
    // Duplicates
    duplicates,
    duplicatesLoading,
    fetchDuplicates,
    setDuplicates,
    // Actions
    doStatusTransition,
    doLevelTransition,
    doMerge,
    mergeLoading,
  };
}
