// -*- coding: utf-8 -*-
/** TaskPlan page — list, detail, SSE updates, demo create. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Tag, Select, Spin } from "antd";
import {
  ReloadOutlined,
  PlusOutlined,
  StopOutlined,
  CaretRightOutlined,
  PauseOutlined,
  PlayCircleOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { taskPlanApi } from "@/api/modules/taskPlan";
import type {
  TaskPlan,
  PlanStatus,
  PlanStepStatus,
} from "@/api/types/taskPlan";
import { PageHeader } from "@/components/PageHeader";
import { useTaskPlanStream } from "./useTaskPlanStream";
import styles from "./index.module.less";

// ── Status config ────────────────────────────────────────────────────────

const PLAN_STATUS_CFG: Record<PlanStatus, { color: string; labelKey: string }> =
  {
    draft: { color: "default", labelKey: "taskPlan.statusDraft" },
    running: { color: "blue", labelKey: "taskPlan.statusRunning" },
    waiting_user: { color: "orange", labelKey: "taskPlan.statusWaitingUser" },
    done: { color: "green", labelKey: "taskPlan.statusDone" },
    failed: { color: "red", labelKey: "taskPlan.statusFailed" },
    cancelled: { color: "default", labelKey: "taskPlan.statusCancelled" },
  };

const STEP_STATUS_CFG: Record<
  PlanStepStatus,
  { color: string; labelKey: string }
> = {
  pending: { color: "default", labelKey: "taskPlan.stepPending" },
  running: { color: "blue", labelKey: "taskPlan.stepRunning" },
  waiting_user: { color: "orange", labelKey: "taskPlan.stepWaitingUser" },
  done: { color: "green", labelKey: "taskPlan.stepDone" },
  failed: { color: "red", labelKey: "taskPlan.stepFailed" },
  cancelled: { color: "default", labelKey: "taskPlan.stepCancelled" },
};

function planStatusTag(status: PlanStatus, t: (k: string) => string) {
  const cfg = PLAN_STATUS_CFG[status] ?? PLAN_STATUS_CFG.draft;
  return <Tag color={cfg.color}>{t(cfg.labelKey)}</Tag>;
}

function stepStatusTag(status: PlanStepStatus, t: (k: string) => string) {
  const cfg = STEP_STATUS_CFG[status] ?? STEP_STATUS_CFG.pending;
  return (
    <Tag color={cfg.color} style={{ fontSize: 11 }}>
      {t(cfg.labelKey)}
    </Tag>
  );
}

function formatTimestamp(ts: number): string {
  if (!ts) return "\u2014";
  return new Date(ts * 1000).toLocaleString();
}

function canCancel(status: PlanStatus): boolean {
  return (
    status === "draft" || status === "running" || status === "waiting_user"
  );
}

// ── Component ───────────────────────────────────────────────────────────────

export default function TaskPlanPage() {
  const { t } = useTranslation();

  const [plans, setPlans] = useState<TaskPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskPlan | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [pausingId, setPausingId] = useState<string | null>(null);
  const [resumingId, setResumingId] = useState<string | null>(null);

  const { lastEvent, connected } = useTaskPlanStream({ enabled: true });

  // ── Fetch list ────────────────────────────────────────────────────────

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const params =
        statusFilter !== "all" ? { status: statusFilter } : undefined;
      const res = await taskPlanApi.listPlans(params);
      setPlans(res.plans);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  // ── Fetch detail ──────────────────────────────────────────────────────

  const fetchDetail = useCallback(async (planId: string) => {
    setDetailLoading(true);
    try {
      const plan = await taskPlanApi.getPlan(planId);
      setDetail(plan);
    } catch {
      /* keep stale */
    } finally {
      setDetailLoading(false);
    }
  }, []);

  // ── Cancel plan ───────────────────────────────────────────────────────

  const cancelPlan = useCallback(
    async (planId: string) => {
      setCancellingId(planId);
      try {
        await taskPlanApi.cancelPlan(planId);
        await fetchList();
        if (selectedId === planId) await fetchDetail(planId);
      } catch {
        /* silent */
      } finally {
        setCancellingId(null);
      }
    },
    [fetchList, fetchDetail, selectedId],
  );

  const startPlan = useCallback(
    async (planId: string) => {
      setStartingId(planId);
      try {
        await taskPlanApi.startPlan(planId);
        await fetchList();
        if (selectedId === planId) await fetchDetail(planId);
      } catch {
        /* silent */
      } finally {
        setStartingId(null);
      }
    },
    [fetchList, fetchDetail, selectedId],
  );

  const pausePlan = useCallback(
    async (planId: string) => {
      setPausingId(planId);
      try {
        await taskPlanApi.pausePlan(planId);
        await fetchList();
        if (selectedId === planId) await fetchDetail(planId);
      } catch {
        /* silent */
      } finally {
        setPausingId(null);
      }
    },
    [fetchList, fetchDetail, selectedId],
  );

  const resumePlanFn = useCallback(
    async (planId: string) => {
      setResumingId(planId);
      try {
        await taskPlanApi.resumePlan(planId);
        await fetchList();
        if (selectedId === planId) await fetchDetail(planId);
      } catch {
        /* silent */
      } finally {
        setResumingId(null);
      }
    },
    [fetchList, fetchDetail, selectedId],
  );

  // ── Create demo plan ──────────────────────────────────────────────────

  const createDemo = useCallback(async () => {
    try {
      await taskPlanApi.createPlan({
        session_id: "demo",
        title: "Demo guided task",
        steps: [
          { title: "Step 1: Gather requirements" },
          { title: "Step 2: Analyze data" },
          { title: "Step 3: Generate report" },
        ],
      });
      await fetchList();
    } catch {
      /* silent */
    }
  }, [fetchList]);

  // ── Initial load ──────────────────────────────────────────────────────

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  // ── SSE-driven refresh (debounced) ──────────────────────────────────

  const sseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!lastEvent) return;
    if (sseTimerRef.current) clearTimeout(sseTimerRef.current);
    sseTimerRef.current = setTimeout(() => {
      fetchList();
      if (selectedId && lastEvent.plan_id === selectedId) {
        fetchDetail(selectedId);
      }
    }, 300);
    return () => {
      if (sseTimerRef.current) clearTimeout(sseTimerRef.current);
    };
  }, [lastEvent, selectedId, fetchList, fetchDetail]);

  // ── Load detail when selected ─────────────────────────────────────────

  useEffect(() => {
    if (selectedId) fetchDetail(selectedId);
    else setDetail(null);
  }, [selectedId, fetchDetail]);

  // ── Status filter options ─────────────────────────────────────────────

  const statusOptions = useMemo(
    () => [
      { value: "all", label: t("taskPlan.filterAll") },
      { value: "draft", label: t("taskPlan.statusDraft") },
      { value: "running", label: t("taskPlan.statusRunning") },
      { value: "waiting_user", label: t("taskPlan.statusWaitingUser") },
      { value: "done", label: t("taskPlan.statusDone") },
      { value: "failed", label: t("taskPlan.statusFailed") },
      { value: "cancelled", label: t("taskPlan.statusCancelled") },
    ],
    [t],
  );

  const displayPlans = useMemo(() => {
    if (!detail) return plans;
    return plans.map((p) => (p.plan_id === detail.plan_id ? detail : p));
  }, [plans, detail]);

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className={styles.page}>
      <PageHeader
        parent={t("nav.control")}
        current={t("taskPlan.pageTitle")}
        extra={
          <div className={styles.headerExtra}>
            <span className={connected ? styles.dotOnline : styles.dotOffline}>
              {connected ? t("taskPlan.connected") : t("taskPlan.disconnected")}
            </span>
          </div>
        }
      />

      <div className={styles.body}>
        {/* Left: plan list */}
        <div className={styles.listPanel}>
          <div className={styles.listToolbar}>
            <Select
              value={statusFilter}
              options={statusOptions}
              style={{ width: 140 }}
              onChange={(v: string) => setStatusFilter(v)}
              size="small"
            />
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={fetchList}
              loading={loading}
            >
              {t("taskPlan.refresh")}
            </Button>
            <Button size="small" icon={<PlusOutlined />} onClick={createDemo}>
              {t("taskPlan.createDemo")}
            </Button>
          </div>

          <div className={styles.listScroll}>
            {loading && displayPlans.length === 0 ? (
              <div className={styles.empty}>
                <Spin />
              </div>
            ) : displayPlans.length === 0 ? (
              <div className={styles.empty}>{t("taskPlan.noPlans")}</div>
            ) : (
              displayPlans.map((plan) => (
                <div
                  key={plan.plan_id}
                  className={`${styles.planCard} ${
                    selectedId === plan.plan_id ? styles.planCardSelected : ""
                  }`}
                  onClick={() => setSelectedId(plan.plan_id)}
                >
                  <div className={styles.planCardHeader}>
                    <span className={styles.planCardTitle}>
                      {plan.title || "\u2014"}
                    </span>
                    {planStatusTag(plan.status, t)}
                  </div>
                  <div className={styles.planCardMeta}>
                    <span className={styles.planCardSteps}>
                      {plan.steps.filter((s) => s.status === "done").length}/
                      {plan.steps.length} {t("taskPlan.stepsLabel")}
                    </span>
                    <span className={styles.planCardTime}>
                      {formatTimestamp(plan.created_at)}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Right: plan detail */}
        <div className={styles.detailPanel}>
          {!selectedId ? (
            <div className={styles.empty}>{t("taskPlan.selectPlan")}</div>
          ) : detailLoading && !detail ? (
            <div className={styles.empty}>
              <Spin />
            </div>
          ) : detail ? (
            <div className={styles.detailScroll}>
              <div className={styles.detailHeader}>
                <h2 className={styles.detailTitle}>{detail.title}</h2>
                <div className={styles.detailActions}>
                  {planStatusTag(detail.status, t)}
                  {detail.status === "draft" && (
                    <Button
                      size="small"
                      type="primary"
                      icon={<CaretRightOutlined />}
                      loading={startingId === detail.plan_id}
                      onClick={() => startPlan(detail.plan_id)}
                    >
                      {t("taskPlan.start")}
                    </Button>
                  )}
                  {detail.status === "running" && (
                    <Button
                      size="small"
                      icon={<PauseOutlined />}
                      loading={pausingId === detail.plan_id}
                      onClick={() => pausePlan(detail.plan_id)}
                    >
                      {t("taskPlan.pause")}
                    </Button>
                  )}
                  {detail.status === "waiting_user" && (
                    <Button
                      size="small"
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      loading={resumingId === detail.plan_id}
                      onClick={() => resumePlanFn(detail.plan_id)}
                    >
                      {t("taskPlan.resume")}
                    </Button>
                  )}
                  {canCancel(detail.status) && (
                    <Button
                      size="small"
                      danger
                      icon={<StopOutlined />}
                      loading={cancellingId === detail.plan_id}
                      onClick={() => cancelPlan(detail.plan_id)}
                    >
                      {t("taskPlan.cancel")}
                    </Button>
                  )}
                </div>
              </div>

              {detail.metadata?.requires_confirmation === true &&
                detail.metadata?.confirmed !== true && (
                  <div
                    style={{
                      padding: "8px 12px",
                      background: "rgba(250,173,20,0.1)",
                      borderRadius: 6,
                      marginBottom: 12,
                      fontSize: 13,
                      color: "#d48806",
                    }}
                  >
                    {t("taskPlan.confirmationRequired")}
                  </div>
                )}

              <div className={styles.detailGrid}>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>plan_id</span>
                  <code className={styles.detailValue}>{detail.plan_id}</code>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>session_id</span>
                  <code className={styles.detailValue}>
                    {detail.session_id}
                  </code>
                </div>
              </div>

              {detail.metadata && Object.keys(detail.metadata).length > 0 && (
                <div className={styles.detailSection}>
                  <h3 className={styles.sectionTitle}>
                    {t("taskPlan.metadata")}
                  </h3>
                  <pre className={styles.preBlock}>
                    {JSON.stringify(detail.metadata, null, 2)}
                  </pre>
                </div>
              )}

              {/* Steps */}
              {detail.steps.length > 0 && (
                <div className={styles.detailSection}>
                  <h3 className={styles.sectionTitle}>
                    {t("taskPlan.steps")} ({detail.steps.length})
                  </h3>
                  <div className={styles.stepsList}>
                    {detail.steps.map((step) => (
                      <div
                        key={step.step_id}
                        className={`${styles.stepRow} ${
                          step.status === "failed"
                            ? styles.stepRowFailed
                            : step.status === "done"
                            ? styles.stepRowDone
                            : detail.current_step_id === step.step_id
                            ? styles.stepRowCurrent
                            : ""
                        }`}
                      >
                        <div className={styles.stepRowHeader}>
                          <span className={styles.stepOrder}>
                            #{step.order + 1}
                          </span>
                          <span className={styles.stepTitle}>{step.title}</span>
                          {stepStatusTag(step.status, t)}
                        </div>
                        {step.description && (
                          <div className={styles.stepDesc}>
                            {step.description}
                          </div>
                        )}
                        <div className={styles.stepMeta}>
                          {step.agent_id && (
                            <span className={styles.stepTag}>
                              agent: {step.agent_id}
                            </span>
                          )}
                          {step.tool_name && (
                            <span className={styles.stepTag}>
                              tool: {step.tool_name}
                            </span>
                          )}
                        </div>
                        {step.error && (
                          <div className={styles.stepError}>{step.error}</div>
                        )}
                      </div>
                    ))}
                    {detail.steps.some((s) => !s.agent_id && !s.tool_name) && (
                      <div className={styles.noAgentHint}>
                        {t("taskPlan.noAgentHint")}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
