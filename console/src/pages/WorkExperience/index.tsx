/** Work Experience management page — maturity-based experience panel. */
import { useEffect, useMemo, useState } from "react";
import { Button, Card, Table, Tag, Select, Drawer, message, Modal } from "@agentscope-ai/design";
import type { ColumnsType } from "antd/es/table";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  InboxOutlined,
  ReloadOutlined,
  MergeOutlined,
  EyeOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { useWorkExperience } from "./useWorkExperience";
import type { WorkExperienceCard } from "@/api/modules/workExperience";
import { PageHeader } from "@/components/PageHeader";
import styles from "./index.module.less";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildStatusOptions(t: (key: string) => string) {
  return [
    { value: "all", label: t("workExperience.filterAllStatuses") },
    { value: "candidate", label: t("workExperience.statusCandidate") },
    { value: "approved", label: t("workExperience.statusApproved") },
    { value: "rejected", label: t("workExperience.statusRejected") },
    { value: "archived", label: t("workExperience.statusArchived") },
  ];
}

function buildLevelOptions(t: (key: string) => string) {
  return [
    { value: "all", label: t("workExperience.filterAllLevels") },
    { value: "new", label: t("workExperience.levelNew") },
    { value: "observed", label: t("workExperience.levelObserved") },
    { value: "mature", label: t("workExperience.levelMature") },
    { value: "deprecated", label: t("workExperience.levelDeprecated") },
  ];
}

function buildScopeOptions(t: (key: string) => string) {
  return [
    { value: "all", label: t("workExperience.filterAllScopes") },
    { value: "global", label: t("workExperience.scopeGlobal") },
    { value: "user", label: t("workExperience.scopeUser") },
    { value: "project", label: t("workExperience.scopeProject") },
    { value: "session", label: t("workExperience.scopeSession") },
  ];
}

function getLevelLabel(t: (key: string) => string, level: string): string {
  const labels: Record<string, string> = {
    new: t("workExperience.levelNew"),
    observed: t("workExperience.levelObserved"),
    mature: t("workExperience.levelMature"),
    deprecated: t("workExperience.levelDeprecated"),
  };
  return labels[level] ?? level;
}

function getStatusLabel(t: (key: string) => string, status: string): string {
  const labels: Record<string, string> = {
    candidate: t("workExperience.statusCandidate"),
    approved: t("workExperience.statusApproved"),
    rejected: t("workExperience.statusRejected"),
    archived: t("workExperience.statusArchived"),
  };
  return labels[status] ?? status;
}

function getScopeLabel(t: (key: string) => string, scope: string): string {
  const labels: Record<string, string> = {
    global: t("workExperience.scopeGlobal"),
    user: t("workExperience.scopeUser"),
    project: t("workExperience.scopeProject"),
    session: t("workExperience.scopeSession"),
  };
  return labels[scope] ?? scope;
}

function LevelBadge({
  level,
  t,
}: {
  level: string;
  t: (key: string) => string;
}) {
  const cfg: Record<string, { color: string; className: string }> = {
    new: { color: "blue", className: styles.levelNew },
    observed: { color: "cyan", className: styles.levelObserved },
    mature: { color: "green", className: styles.levelMature },
    deprecated: { color: "default", className: styles.levelDeprecated },
  };
  const c = cfg[level] ?? { color: "default", className: "" };
  return <Tag color={c.color}>{getLevelLabel(t, level)}</Tag>;
}

function StatusBadge({
  status,
  t,
}: {
  status: string;
  t: (key: string) => string;
}) {
  const cfg: Record<string, { color: string; className: string }> = {
    candidate: { color: "orange", className: styles.statusCandidate },
    approved: { color: "green", className: styles.statusApproved },
    rejected: { color: "red", className: styles.statusRejected },
    archived: { color: "default", className: styles.statusArchived },
  };
  const c = cfg[status] ?? { color: "default", className: "" };
  return <Tag color={c.color}>{getStatusLabel(t, status)}</Tag>;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Detail Drawer
// ---------------------------------------------------------------------------

interface DetailDrawerProps {
  card: WorkExperienceCard | null;
  loading: boolean;
  onClose: () => void;
  onStatusAction: (
    cardId: string,
    action: "approve" | "reject" | "archive" | "reactivate",
  ) => Promise<void>;
  onLevelAction: (
    cardId: string,
    action: "promote" | "demote" | "deprecate",
  ) => Promise<void>;
  onShowDuplicates: (cardId: string) => void;
  duplicates: import("@/api/modules/workExperience").DuplicateDetectionResponse | null;
  duplicatesLoading: boolean;
  onMergeClick: (
    sourceId: string,
    targetId: string | null,
    options: { value: string; label: string }[],
  ) => void;
  t: (key: string) => string;
}

function DetailDrawer({
  card,
  loading,
  onClose,
  onStatusAction,
  onLevelAction,
  onShowDuplicates,
  duplicates,
  duplicatesLoading,
  onMergeClick,
  t,
}: DetailDrawerProps) {
  if (!card) return null;

  // Level transition buttons
  const canPromote = card.experience_level === "new" || card.experience_level === "observed";
  const canDemote = card.experience_level === "observed" || card.experience_level === "mature";
  const canDeprecate = card.experience_level !== "deprecated";

  // Status transition buttons
  const canApprove = card.status === "candidate";
  const canReject = card.status === "candidate" || card.status === "approved";
  const canArchive = card.status !== "archived";
  const canReactivate = card.status === "rejected";

  const duplicateOptions = duplicates?.duplicates.map((d) => ({
    label: `${d.title} (${d.trigger_hint}) — ${t("workExperience.maturityScore")}: ${d.maturity_score.toFixed(1)}`,
    value: d.experience_id,
  })) ?? [];

  const firstDuplicateId = duplicates?.duplicates[0]?.experience_id ?? null;

  const handleMergeClick = () => {
    if (!duplicates || duplicates.count === 0) return;
    onMergeClick(card.experience_id, firstDuplicateId, duplicateOptions);
  };

  return (
    <>
      <Drawer
        title={card.title}
        placement="right"
        width={600}
        onClose={onClose}
        open={!!card}
      >
        {loading ? (
          <div style={{ textAlign: "center", padding: 40 }}>{t("workExperience.loading")}</div>
        ) : (
          <>
            {/* Maturity info */}
            <div className={styles.drawerSection}>
              <div className={styles.drawerSectionTitle}>{t("workExperience.maturity")}</div>
              <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                <LevelBadge level={card.experience_level} t={t} />
                <span style={{ fontSize: 13, color: "#666" }}>
                  {t("workExperience.status")}: <StatusBadge status={card.status} t={t} />
                </span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 16px", fontSize: 13 }}>
                <div><span style={{ color: "#999" }}>{t("workExperience.maturityScore")}: </span>{card.maturity_score.toFixed(1)}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.successRate")}: </span>{(card.success_rate_estimate * 100).toFixed(0)}%</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.effectiveRatio")}: </span>{card.effective_count > 0 ? (card.effective_count / card.hit_count).toFixed(2) : "—"}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.scope")}: </span>{getScopeLabel(t, card.scope)}</div>
              </div>
            </div>

            {/* Work guidance */}
            {(card.usage_pattern_summary || card.recommended_tool_order.length > 0 || card.recommended_workflow.length > 0) && (
              <div className={styles.drawerSection}>
                <div className={styles.drawerSectionTitle}>{t("workExperience.workGuidance")}</div>
                {card.usage_pattern_summary && (
                  <div className={styles.drawerField}>
                    <div className={styles.drawerFieldLabel}>{t("workExperience.patternSummary")}</div>
                    <div className={styles.drawerFieldValue}>{card.usage_pattern_summary}</div>
                  </div>
                )}
                {card.recommended_tool_order.length > 0 && (
                  <div className={styles.drawerField}>
                    <div className={styles.drawerFieldLabel}>{t("workExperience.recommendedTools")}</div>
                    <div className={styles.tagList}>
                      {card.recommended_tool_order.map((tool: string) => (
                        <Tag key={tool} color="blue">{tool}</Tag>
                      ))}
                    </div>
                  </div>
                )}
                {card.recommended_workflow.length > 0 && (
                  <div className={styles.drawerField}>
                    <div className={styles.drawerFieldLabel}>{t("workExperience.recommendedWorkflow")}</div>
                    <div className={styles.tagList}>
                      {card.recommended_workflow.map((step: string) => (
                        <Tag key={step} color="purple">{step}</Tag>
                      ))}
                    </div>
                  </div>
                )}
                {card.applicable_task_types.length > 0 && (
                  <div className={styles.drawerField}>
                    <div className={styles.drawerFieldLabel}>{t("workExperience.taskTypes")}</div>
                    <div className={styles.tagList}>
                      {card.applicable_task_types.map((type: string) => (
                        <Tag key={type} color="cyan">{type}</Tag>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Trigger */}
            <div className={styles.drawerSection}>
              <div className={styles.drawerSectionTitle}>{t("workExperience.trigger")}</div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldHint")}</div>
                <div className={styles.drawerFieldValue}>{card.trigger_hint}</div>
              </div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldKeywords")}</div>
                <div className={styles.tagList}>
                  {card.trigger_keywords.map((k: string) => (
                    <Tag key={k} className={styles.keywordTag} color="purple">{k}</Tag>
                  ))}
                </div>
              </div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldApplicabilityTags")}</div>
                <div className={styles.tagList}>
                  {card.applicability_tags.map((tag: string) => (
                    <Tag key={tag} className={styles.actionTag}>{tag}</Tag>
                  ))}
                </div>
              </div>
            </div>

            {/* Content */}
            <div className={styles.drawerSection}>
              <div className={styles.drawerSectionTitle}>{t("workExperience.content")}</div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldWhatHappened")}</div>
                <div className={styles.drawerFieldValue}>{card.what_happened}</div>
              </div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldGuidance")}</div>
                <div className={styles.drawerFieldValue}>{card.guidance}</div>
              </div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldWhatWorked")}</div>
                <div className={styles.tagList}>
                  {card.what_worked.map((w: string) => (
                    <Tag key={w} color="green" className={styles.actionTag}>{w}</Tag>
                  ))}
                </div>
              </div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldWhatFailed")}</div>
                <div className={styles.tagList}>
                  {card.what_failed.map((f: string) => (
                    <Tag key={f} color="red" className={styles.actionTag}>{f}</Tag>
                  ))}
                </div>
              </div>
              <div className={styles.drawerField}>
                <div className={styles.drawerFieldLabel}>{t("workExperience.fieldAvoidance")}</div>
                <div className={styles.drawerFieldValue}>{card.avoidance}</div>
              </div>
            </div>

            {/* Usage stats */}
            <div className={styles.drawerSection}>
              <div className={styles.drawerSectionTitle}>{t("workExperience.usageStats")}</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 16px", fontSize: 13 }}>
                <div><span style={{ color: "#999" }}>{t("workExperience.hitCount")}: </span>{card.hit_count}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.effectiveCount")}: </span>{card.effective_count}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.confidence")}: </span>{card.confidence.toFixed(2)}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.qualityScore")}: </span>{card.quality_score.toFixed(3)}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.fieldLastRetrieved")}: </span>{formatDate(card.last_retrieved_at)}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.fieldLastUsed")}: </span>{formatDate(card.last_used_at)}</div>
              </div>
            </div>

            {/* Metadata */}
            <div className={styles.drawerSection}>
              <div className={styles.drawerSectionTitle}>{t("workExperience.metadata")}</div>
              <div style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 4 }}>
                <div><span style={{ color: "#999" }}>{t("workExperience.fieldCardId")}: </span><code>{card.experience_id}</code></div>
                <div><span style={{ color: "#999" }}>{t("workExperience.fieldSourceTask")}: </span>{card.source_task_id}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.fieldSession")}: </span>{card.source_session_id}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.fieldCreated")}: </span>{formatDate(card.created_at)}</div>
                <div><span style={{ color: "#999" }}>{t("workExperience.fieldUpdated")}: </span>{formatDate(card.updated_at)}</div>
              </div>
            </div>

            {/* Level transitions (new maturity model) */}
            <div className={styles.drawerSection}>
              <div className={styles.drawerSectionTitle}>{t("workExperience.levelTransitions")}</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {canPromote && (
                  <Button type="primary" icon={<ArrowUpOutlined />} onClick={() => onLevelAction(card.experience_id, "promote")}>
                    {t("workExperience.promote")}
                  </Button>
                )}
                {canDemote && (
                  <Button icon={<ArrowDownOutlined />} onClick={() => onLevelAction(card.experience_id, "demote")}>
                    {t("workExperience.demote")}
                  </Button>
                )}
                {canDeprecate && (
                  <Button danger icon={<StopOutlined />} onClick={() => onLevelAction(card.experience_id, "deprecate")}>
                    {t("workExperience.deprecate")}
                  </Button>
                )}
              </div>
            </div>

            {/* Legacy status transitions — secondary, kept for backward compatibility */}
            <div className={styles.drawerSection} style={{ opacity: 0.75 }}>
              <div className={styles.drawerSectionTitle}>
                <span style={{ fontSize: 12, color: "#999", fontWeight: 400 }}>
                  {t("workExperience.statusTransitions")} · Legacy
                </span>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {canApprove && (
                  <Button size="small" icon={<CheckCircleOutlined />} onClick={() => onStatusAction(card.experience_id, "approve")}>
                    {t("workExperience.approve")}
                  </Button>
                )}
                {canReject && (
                  <Button size="small" danger icon={<CloseCircleOutlined />} onClick={() => onStatusAction(card.experience_id, "reject")}>
                    {t("workExperience.reject")}
                  </Button>
                )}
                {canArchive && (
                  <Button size="small" icon={<InboxOutlined />} onClick={() => onStatusAction(card.experience_id, "archive")}>
                    {t("workExperience.archive")}
                  </Button>
                )}
                {canReactivate && (
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => onStatusAction(card.experience_id, "reactivate")}>
                    {t("workExperience.reactivate")}
                  </Button>
                )}
              </div>
            </div>

            {/* Duplicates */}
            <div className={styles.drawerSection}>
              <div className={styles.drawerSectionTitle}>
                {t("workExperience.potentialDuplicates")} ({duplicates?.count ?? 0})
              </div>
              {duplicatesLoading ? (
                <div>{t("workExperience.loading")}</div>
              ) : duplicates && duplicates.count > 0 ? (
                <>
                  {duplicates.duplicates.map((dup) => (
                    <div key={dup.experience_id} className={styles.duplicateCard}>
                      <div className={styles.duplicateTitle}>{dup.title}</div>
                      <div className={styles.duplicateMeta}>
                        {dup.trigger_hint} · {t("workExperience.maturityScore")}: {dup.maturity_score.toFixed(1)} · <LevelBadge level={dup.experience_level} t={t} />
                      </div>
                    </div>
                  ))}
                  <Button icon={<MergeOutlined />} style={{ marginTop: 8 }} onClick={handleMergeClick}>
                    {t("workExperience.mergeIntoFirstDuplicate")}
                  </Button>
                </>
              ) : (
                <div style={{ color: "#999", fontSize: 13 }}>{t("workExperience.noDuplicatesFound")}</div>
              )}
              {!duplicates && !duplicatesLoading && (
                <Button icon={<EyeOutlined />} onClick={() => onShowDuplicates(card.experience_id)}>
                  {t("workExperience.checkDuplicates")}
                </Button>
              )}
            </div>
          </>
        )}
      </Drawer>
    </>
  );
}

// ---------------------------------------------------------------------------
// Stats Cards
// ---------------------------------------------------------------------------

function StatsBar({
  stats,
  t,
}: {
  stats: import("@/api/modules/workExperience").WorkExperienceStats | null;
  t: (key: string) => string;
}) {
  if (!stats) return null;
  return (
    <div className={styles.statsBar}>
      <div className={styles.statCard}>
        <div className={styles.statValue}>{stats.total_cards}</div>
        <div className={styles.statLabel}>{t("workExperience.totalCards")}</div>
      </div>
      <div className={styles.statCard}>
        <div className={styles.statValue}>{stats.by_level["mature"] ?? 0}</div>
        <div className={styles.statLabel}>{t("workExperience.levelMature")}</div>
      </div>
      <div className={styles.statCard}>
        <div className={styles.statValue}>{stats.by_level["observed"] ?? 0}</div>
        <div className={styles.statLabel}>{t("workExperience.levelObserved")}</div>
      </div>
      <div className={styles.statCard}>
        <div className={styles.statValue}>{stats.by_level["new"] ?? 0}</div>
        <div className={styles.statLabel}>{t("workExperience.levelNew")}</div>
      </div>
      <div className={styles.statCard}>
        <div className={styles.statValue}>{stats.avg_maturity_score.toFixed(1)}</div>
        <div className={styles.statLabel}>{t("workExperience.avgMaturity")}</div>
      </div>
      <div className={styles.statCard}>
        <div className={styles.statValue}>{stats.total_hits}</div>
        <div className={styles.statLabel}>{t("workExperience.totalHits")}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function WorkExperiencePage() {
  const { t } = useTranslation();
  const [messageApi, contextHolder] = message.useMessage();

  // Merge modal state — lifted here to avoid stale closure in DetailDrawer's imperative modal
  const [mergeModalOpen, setMergeModalOpen] = useState(false);
  const [mergeSourceId, setMergeSourceId] = useState<string | null>(null);
  const [mergeTargetId, setMergeTargetId] = useState<string | null>(null);
  const [mergeOptions, setMergeOptions] = useState<{ value: string; label: string }[]>([]);

  const {
    cards,
    total,
    loading,
    page,
    pageSize,
    fetchCards,
    stats,
    fetchStats,
    statusFilter,
    levelFilter,
    scopeFilter,
    handleFilterChange,
    selectedCard,
    drawerLoading,
    fetchCardDetail,
    setSelectedCard,
    duplicates,
    duplicatesLoading,
    fetchDuplicates,
    setDuplicates,
    doStatusTransition,
    doLevelTransition,
    doMerge,
    mergeLoading,
  } = useWorkExperience();

  const handleMergeClick = (
    sourceId: string,
    targetId: string | null,
    options: { value: string; label: string }[],
  ) => {
    setMergeSourceId(sourceId);
    setMergeTargetId(targetId);
    setMergeOptions(options);
    setMergeModalOpen(true);
  };

  const handleMergeOk = async () => {
    if (!mergeSourceId || !mergeTargetId) return;
    try {
      await doMerge(mergeSourceId, mergeTargetId);
      setMergeModalOpen(false);
      setMergeSourceId(null);
      setMergeTargetId(null);
    } catch {
      // error handled in hook
    }
  };

  const handleMergeClose = () => {
    setMergeModalOpen(false);
    setMergeSourceId(null);
    setMergeTargetId(null);
  };

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  const columns: ColumnsType<WorkExperienceCard> = useMemo(
    () => [
      {
        title: t("workExperience.title"),
        dataIndex: "title",
        key: "title",
        ellipsis: true,
        width: 200,
      },
      {
        title: t("workExperience.experienceLevel"),
        dataIndex: "experience_level",
        key: "experience_level",
        width: 100,
        render: (level: string) => <LevelBadge level={level} t={t} />,
      },
      {
        title: t("workExperience.scope"),
        dataIndex: "scope",
        key: "scope",
        width: 80,
        render: (scope: string) => getScopeLabel(t, scope),
      },
      {
        title: t("workExperience.maturityScore"),
        dataIndex: "maturity_score",
        key: "maturity_score",
        width: 100,
        render: (v: number) => v.toFixed(1),
      },
      {
        title: t("workExperience.hitCount"),
        dataIndex: "hit_count",
        key: "hit_count",
        width: 80,
      },
      {
        title: t("workExperience.effectiveCount"),
        dataIndex: "effective_count",
        key: "effective_count",
        width: 100,
      },
      {
        title: t("workExperience.successRate"),
        dataIndex: "success_rate_estimate",
        key: "success_rate_estimate",
        width: 90,
        render: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        title: t("workExperience.triggerHint"),
        dataIndex: "trigger_hint",
        key: "trigger_hint",
        ellipsis: true,
      },
      {
        title: t("workExperience.actions"),
        key: "actions",
        width: 80,
        render: (_: unknown, record: WorkExperienceCard) => (
          <Button size="small" onClick={() => fetchCardDetail(record.experience_id)}>
            {t("workExperience.view")}
          </Button>
        ),
      },
    ],
    [t, fetchCardDetail],
  );

  const handleStatusAction = async (
    cardId: string,
    action: "approve" | "reject" | "archive" | "reactivate",
  ) => {
    try {
      await doStatusTransition(cardId, action);
      messageApi.success(t(`workExperience.${action}Success`));
    } catch {
      // error handled in hook
    }
  };

  const handleLevelAction = async (
    cardId: string,
    action: "promote" | "demote" | "deprecate",
  ) => {
    try {
      await doLevelTransition(cardId, action);
      messageApi.success(t(`workExperience.${action}Success`));
    } catch {
      // error handled in hook
    }
  };

  return (
    <>
      {contextHolder}
      <div className={styles.workExperiencePage}>
        <PageHeader parent={t("nav.agent")} current={t("workExperience.pageTitle")} />

        <StatsBar stats={stats} t={t} />

        <div className={styles.filters}>
          <div className={styles.filterGroup}>
            <span className={styles.filterLabel}>{t("workExperience.filterLevel")}</span>
            <Select
              value={levelFilter}
              options={buildLevelOptions(t)}
              style={{ width: 160 }}
              onChange={(val) => handleFilterChange(statusFilter, val as "all" | "new" | "observed" | "mature" | "deprecated", scopeFilter, false)}
            />
          </div>
          <div className={styles.filterGroup}>
            <span className={styles.filterLabel}>{t("workExperience.filterScope")}</span>
            <Select
              value={scopeFilter}
              options={buildScopeOptions(t)}
              style={{ width: 140 }}
              onChange={(val) => handleFilterChange(statusFilter, levelFilter, val as "all" | "global" | "user" | "project" | "session", false)}
            />
          </div>
          <div className={styles.filterGroup}>
            <span className={styles.filterLabel} style={{ color: "#999", fontSize: 12 }}>
              {t("workExperience.status")}:
            </span>
            <Select
              value={statusFilter}
              options={buildStatusOptions(t)}
              style={{ width: 140 }}
              onChange={(val) => handleFilterChange(val as "all" | "candidate" | "approved" | "rejected" | "archived", levelFilter, scopeFilter, false)}
            />
          </div>
          <Button icon={<ReloadOutlined />} onClick={() => fetchCards(1)}>
            {t("workExperience.refresh")}
          </Button>
        </div>

        <Card className={styles.tableCard}>
          <Table
            columns={columns}
            dataSource={cards}
            rowKey="experience_id"
            loading={loading}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: false,
              showTotal: (tot: number) => `${tot} ${t("workExperience.totalCardsLabel")}`,
              onChange: (p) => fetchCards(p),
            }}
            scroll={{ x: 900 }}
          />
        </Card>
      </div>

      <DetailDrawer
        card={selectedCard}
        loading={drawerLoading}
        onClose={() => setSelectedCard(null)}
        onStatusAction={handleStatusAction}
        onLevelAction={handleLevelAction}
        onShowDuplicates={(cardId) => {
          fetchDuplicates(cardId);
          setDuplicates(null);
        }}
        duplicates={duplicates}
        duplicatesLoading={duplicatesLoading}
        onMergeClick={handleMergeClick}
        t={t}
      />

      {/* Controlled merge modal — avoids stale closure bug of Modal.confirm() */}
      <Modal
        open={mergeModalOpen}
        title={t("workExperience.mergeCards")}
        onOk={handleMergeOk}
        onCancel={handleMergeClose}
        okText={t("workExperience.merge")}
        confirmLoading={mergeLoading}
        okButtonProps={{ disabled: !mergeTargetId }}
      >
        <p style={{ marginBottom: 8 }}>
          {t("workExperience.mergeDescription")}
        </p>
        <Select
          value={mergeTargetId}
          options={mergeOptions}
          style={{ width: "100%" }}
          placeholder={t("workExperience.selectTargetCard")}
          onChange={(val) => setMergeTargetId(val)}
        />
      </Modal>
    </>
  );
}
