// -*- coding: utf-8 -*-
/**
 * ChatPlanPanel — collapsible right panel showing task plans for the
 * current chat session. Allows viewing, cancelling, and inserting
 * new instruction steps.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Tag, Drawer, Input } from "antd";
import {
  StopOutlined,
  PlusOutlined,
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
import { useTaskPlanStream } from "@/pages/TaskPlan/useTaskPlanStream";
import styles from "./index.module.less";

// ── Status config ──────────────────────────────────────────────────────────

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

const VISIBLE_PLAN_STATUSES = new Set<PlanStatus>([
  "draft",
  "running",
  "waiting_user",
]);

function planTag(status: PlanStatus, t: (k: string) => string) {
  const cfg = PLAN_STATUS_CFG[status] ?? PLAN_STATUS_CFG.draft;
  return (
    <Tag color={cfg.color} style={{ fontSize: 11 }}>
      {t(cfg.labelKey)}
    </Tag>
  );
}

function stepTag(status: PlanStepStatus, t: (k: string) => string) {
  const cfg = STEP_STATUS_CFG[status] ?? STEP_STATUS_CFG.pending;
  return (
    <Tag color={cfg.color} style={{ fontSize: 10, lineHeight: "16px" }}>
      {t(cfg.labelKey)}
    </Tag>
  );
}

function canCancel(status: PlanStatus): boolean {
  return (
    status === "draft" || status === "running" || status === "waiting_user"
  );
}

// ── Props ──────────────────────────────────────────────────────────────────

export interface ChatPlanPanelProps {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}

// ── Component ──────────────────────────────────────────────────────────────

export default function ChatPlanPanel({
  sessionId: _sessionId,
  open,
  onClose,
}: ChatPlanPanelProps) {
  const { t } = useTranslation();

  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 768,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)");
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    setIsMobile(mq.matches);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const { lastEvent, connected } = useTaskPlanStream({ enabled: open });

  const [plans, setPlans] = useState<TaskPlan[]>([]);
  const [, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [pausingId, setPausingId] = useState<string | null>(null);
  const [resumingId, setResumingId] = useState<string | null>(null);
  const [insertingPlanId, setInsertingPlanId] = useState<string | null>(null);
  const [insertText, setInsertText] = useState("");
  const fetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Fetch plans ────────────────────────────────────────────────────────

  const fetchPlans = useCallback(async () => {
    setLoading(true);
    try {
      const res = await taskPlanApi.listPlans({ limit: 20 });
      setPlans(res.plans);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Debounced SSE refresh ──────────────────────────────────────────────

  useEffect(() => {
    if (!lastEvent) return;
    if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current);
    fetchTimerRef.current = setTimeout(() => {
      fetchPlans();
    }, 300);
    return () => {
      if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current);
    };
  }, [lastEvent, fetchPlans]);

  // ── Load on open ───────────────────────────────────────────────────────

  useEffect(() => {
    if (open) fetchPlans();
  }, [open, fetchPlans]);

  // ── Cancel plan ────────────────────────────────────────────────────────

  const cancelPlan = useCallback(
    async (planId: string) => {
      setCancellingId(planId);
      try {
        await taskPlanApi.cancelPlan(planId);
        await fetchPlans();
      } catch {
        /* silent */
      } finally {
        setCancellingId(null);
      }
    },
    [fetchPlans],
  );

  // ── Start plan ─────────────────────────────────────────────────────────

  const startPlan = useCallback(
    async (planId: string) => {
      setStartingId(planId);
      try {
        await taskPlanApi.startPlan(planId);
        await fetchPlans();
      } catch {
        /* silent */
      } finally {
        setStartingId(null);
      }
    },
    [fetchPlans],
  );

  // ── Pause / Resume plan ────────────────────────────────────────────────

  const pausePlan = useCallback(
    async (planId: string) => {
      setPausingId(planId);
      try {
        await taskPlanApi.pausePlan(planId);
        await fetchPlans();
      } catch {
        /* silent */
      } finally {
        setPausingId(null);
      }
    },
    [fetchPlans],
  );

  const resumePlanFn = useCallback(
    async (planId: string) => {
      setResumingId(planId);
      try {
        await taskPlanApi.resumePlan(planId);
        await fetchPlans();
      } catch {
        /* silent */
      } finally {
        setResumingId(null);
      }
    },
    [fetchPlans],
  );

  // ── Add instruction step ───────────────────────────────────────────────

  const addInstruction = useCallback(
    async (planId: string) => {
      const text = insertText.trim();
      if (!text) return;
      setInsertingPlanId(planId);
      try {
        await taskPlanApi.addStep(planId, {
          title: text,
          description: "",
          metadata: { inserted_from_chat: true },
        });
        setInsertText("");
        await fetchPlans();
      } catch {
        /* silent */
      } finally {
        setInsertingPlanId(null);
      }
    },
    [insertText, fetchPlans],
  );

  // ── Stats ──────────────────────────────────────────────────────────────

  const stats = useMemo(() => {
    const s = { running: 0, waiting: 0 };
    for (const p of plans) {
      if (p.status === "running") s.running++;
      else if (p.status === "waiting_user") s.waiting++;
    }
    return s;
  }, [plans]);

  const visiblePlans = useMemo(
    () => plans.filter((plan) => VISIBLE_PLAN_STATUSES.has(plan.status)),
    [plans],
  );

  // ── Panel content ──────────────────────────────────────────────────────

  const content = (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <span className={styles.panelTitle}>{t("chatPlan.panelTitle")}</span>
        <span className={connected ? styles.dotOnline : styles.dotOffline}>
          {connected ? t("taskPlan.connected") : t("taskPlan.disconnected")}
        </span>
      </div>

      {stats.running + stats.waiting > 0 && (
        <div className={styles.statsBar}>
          {stats.running > 0 && (
            <span className={styles.stat} data-status="running">
              {stats.running} {t("taskPlan.statusRunning")}
            </span>
          )}
          {stats.waiting > 0 && (
            <span className={styles.stat} data-status="waiting">
              {stats.waiting} {t("taskPlan.statusWaitingUser")}
            </span>
          )}
        </div>
      )}

      <div className={styles.planList}>
        {visiblePlans.length === 0 ? (
          <div className={styles.empty}>{t("taskPlan.noPlans")}</div>
        ) : (
          visiblePlans.map((plan) => {
            const doneCount = plan.steps.filter(
              (s) => s.status === "done",
            ).length;
            const isExpanded = expandedId === plan.plan_id;
            return (
              <div key={plan.plan_id} className={styles.planCard}>
                <div
                  className={styles.planCardHeader}
                  onClick={() =>
                    setExpandedId(isExpanded ? null : plan.plan_id)
                  }
                >
                  <span className={styles.planCardTitle}>
                    {plan.title || "\u2014"}
                  </span>
                  <span className={styles.cardActions}>
                    {planTag(plan.status, t)}
                    {plan.status === "draft" && (
                      <Button
                        size="small"
                        type="text"
                        icon={<CaretRightOutlined />}
                        loading={startingId === plan.plan_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          startPlan(plan.plan_id);
                        }}
                        title={t("taskPlan.start")}
                      />
                    )}
                    {plan.status === "running" && (
                      <Button
                        size="small"
                        type="text"
                        icon={<PauseOutlined />}
                        loading={pausingId === plan.plan_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          pausePlan(plan.plan_id);
                        }}
                        title={t("taskPlan.pause")}
                      />
                    )}
                    {plan.status === "waiting_user" && (
                      <Button
                        size="small"
                        type="text"
                        icon={<PlayCircleOutlined />}
                        loading={resumingId === plan.plan_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          resumePlanFn(plan.plan_id);
                        }}
                        title={t("taskPlan.resume")}
                      />
                    )}
                    {canCancel(plan.status) && (
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<StopOutlined />}
                        loading={cancellingId === plan.plan_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          cancelPlan(plan.plan_id);
                        }}
                        title={t("taskPlan.cancel")}
                      />
                    )}
                  </span>
                </div>
                <div
                  className={styles.planCardMeta}
                  onClick={() =>
                    setExpandedId(isExpanded ? null : plan.plan_id)
                  }
                >
                  <span className={styles.planCardProgress}>
                    {doneCount}/{plan.steps.length} {t("taskPlan.stepsLabel")}
                  </span>
                </div>

                {plan.metadata?.requires_confirmation === true &&
                  plan.metadata?.confirmed !== true && (
                    <div
                      style={{
                        padding: "4px 8px",
                        background: "rgba(250,173,20,0.1)",
                        borderRadius: 4,
                        fontSize: 11,
                        color: "#d48806",
                        margin: "4px 4px 0",
                      }}
                    >
                      {t("taskPlan.confirmationRequired")}
                    </div>
                  )}

                {/* Expanded steps */}
                {isExpanded && (
                  <div className={styles.stepsSection}>
                    {plan.steps.length > 0 && (
                      <div className={styles.stepsList}>
                        {plan.steps.map((step) => (
                          <div key={step.step_id} className={styles.stepRow}>
                            <span className={styles.stepOrder}>
                              #{step.order + 1}
                            </span>
                            <span className={styles.stepTitle}>
                              {step.title}
                            </span>
                            {stepTag(step.status, t)}
                          </div>
                        ))}
                        {plan.steps.some(
                          (s) => !s.agent_id && !s.tool_name,
                        ) && (
                          <div className={styles.noAgentHint}>
                            {t("taskPlan.noAgentHint")}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Add instruction */}
                    <div className={styles.insertRow}>
                      <Input
                        size="small"
                        placeholder={t("chatPlan.addPlaceholder")}
                        value={expandedId === plan.plan_id ? insertText : ""}
                        onChange={(e) => setInsertText(e.target.value)}
                        onPressEnter={() => addInstruction(plan.plan_id)}
                        disabled={insertingPlanId === plan.plan_id}
                      />
                      <Button
                        size="small"
                        type="primary"
                        icon={<PlusOutlined />}
                        loading={insertingPlanId === plan.plan_id}
                        onClick={() => addInstruction(plan.plan_id)}
                      >
                        {t("chatPlan.addBtn")}
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );

  if (isMobile) {
    return (
      <Drawer
        title={t("chatPlan.panelTitle")}
        placement="right"
        onClose={onClose}
        open={open}
        width={360}
        styles={{ body: { padding: 0 } }}
      >
        {content}
      </Drawer>
    );
  }

  return (
    <div
      className={`${styles.inlinePanel} ${open ? styles.inlinePanelOpen : ""}`}
    >
      <div className={styles.panelInner}>
        <button
          className={styles.closeBtn}
          onClick={onClose}
          title={t("chatPlan.close")}
        >
          {"\u2715"}
        </button>
        {content}
      </div>
    </div>
  );
}
