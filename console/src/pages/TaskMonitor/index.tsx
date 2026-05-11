// -*- coding: utf-8 -*-
/** Task Monitor page — list, detail, real-time SSE updates. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Tag, Select, Spin } from "antd";
import { ReloadOutlined, StopOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { taskMonitorApi } from "@/api/modules/taskMonitor";
import type { Task, TaskStatus } from "@/api/types/taskMonitor";
import { PageHeader } from "@/components/PageHeader";
import { useTaskMonitorStream } from "./useTaskMonitorStream";
import styles from "./index.module.less";

// ── Status config ────────────────────────────────────────────────────────

const STATUS_CFG: Record<TaskStatus, { color: string; labelKey: string }> = {
  pending: { color: "default", labelKey: "taskMonitor.statusPending" },
  running: { color: "blue", labelKey: "taskMonitor.statusRunning" },
  waiting: { color: "orange", labelKey: "taskMonitor.statusWaiting" },
  done: { color: "green", labelKey: "taskMonitor.statusDone" },
  failed: { color: "red", labelKey: "taskMonitor.statusFailed" },
  cancelled: { color: "default", labelKey: "taskMonitor.statusCancelled" },
};

function statusTag(status: TaskStatus, t: (k: string) => string) {
  const cfg = STATUS_CFG[status] ?? STATUS_CFG.pending;
  return <Tag color={cfg.color}>{t(cfg.labelKey)}</Tag>;
}

// ── Time helpers ─────────────────────────────────────────────────────────

function formatTimestamp(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

function formatDuration(created: number, finished?: number): string {
  const end = finished || Date.now() / 1000;
  const diff = Math.max(0, Math.round(end - created));
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`;
  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  return `${h}h ${m}m`;
}

// ── Event type icon char ─────────────────────────────────────────────────

const EVENT_ICON: Record<string, string> = {
  task_created: "+",
  task_updated: "~",
  stage_started: "▶",
  stage_completed: "✓",
  log: "•",
  error: "!",
  task_done: "✓",
  task_failed: "✗",
  task_cancelled: "■",
};

function canCancelTask(task: Task): boolean {
  return isActiveTask(task);
}

function isActiveTask(task: Task): boolean {
  return (
    task.status === "pending" ||
    task.status === "running" ||
    task.status === "waiting"
  );
}

// ── Main Component ───────────────────────────────────────────────────────

export default function TaskMonitorPage() {
  const { t } = useTranslation();

  // List state
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  // Detail state
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Task | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // SSE
  const { lastEvent, connected } = useTaskMonitorStream({ enabled: true });

  // ── Fetch list ────────────────────────────────────────────────────────

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const params =
        statusFilter !== "all" ? { status: statusFilter } : undefined;
      const res = await taskMonitorApi.listTasks(params);
      setTasks(res.tasks.filter(isActiveTask));
    } catch {
      // Silently fail — show stale data
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  // ── Fetch detail ──────────────────────────────────────────────────────

  const fetchDetail = useCallback(async (taskId: string) => {
    setDetailLoading(true);
    try {
      const task = await taskMonitorApi.getTask(taskId);
      setDetail(task);
    } catch {
      // Keep stale detail
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const cancelTask = useCallback(
    async (taskId: string) => {
      setCancellingId(taskId);
      try {
        await taskMonitorApi.cancelTask(taskId);
        await fetchList();
        if (selectedId === taskId) {
          await fetchDetail(taskId);
        }
      } catch {
        // Keep stale data; SSE or the next manual refresh will reconcile.
      } finally {
        setCancellingId(null);
      }
    },
    [fetchList, fetchDetail, selectedId],
  );

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
      if (selectedId && lastEvent.task_id === selectedId) {
        fetchDetail(selectedId);
      }
    }, 300);
    return () => {
      if (sseTimerRef.current) clearTimeout(sseTimerRef.current);
    };
  }, [lastEvent, selectedId, fetchList, fetchDetail]);

  // ── Load detail when selected ─────────────────────────────────────────

  useEffect(() => {
    if (selectedId) {
      fetchDetail(selectedId);
    } else {
      setDetail(null);
    }
  }, [selectedId, fetchDetail]);

  useEffect(() => {
    if (detail && !isActiveTask(detail)) {
      setSelectedId(null);
      setDetail(null);
    }
  }, [detail]);

  // ── Status filter options ─────────────────────────────────────────────

  const statusOptions = useMemo(
    () => [
      { value: "all", label: t("taskMonitor.filterAll") },
      { value: "pending", label: t("taskMonitor.statusPending") },
      { value: "running", label: t("taskMonitor.statusRunning") },
      { value: "waiting", label: t("taskMonitor.statusWaiting") },
    ],
    [t],
  );

  // ── Merge detail into list for real-time status ───────────────────────

  const displayTasks = useMemo(() => {
    if (!detail || !isActiveTask(detail)) return tasks;
    return tasks.map((t) => (t.task_id === detail.task_id ? detail : t));
  }, [tasks, detail]);

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className={styles.page}>
      <PageHeader
        parent={t("nav.control")}
        current={t("taskMonitor.pageTitle")}
        extra={
          <div className={styles.headerExtra}>
            <span className={connected ? styles.dotOnline : styles.dotOffline}>
              {connected
                ? t("taskMonitor.connected")
                : t("taskMonitor.disconnected")}
            </span>
          </div>
        }
      />

      <div className={styles.body}>
        {/* Left: task list */}
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
              {t("taskMonitor.refresh")}
            </Button>
          </div>

          <div className={styles.listScroll}>
            {loading && displayTasks.length === 0 ? (
              <div className={styles.empty}>
                <Spin />
              </div>
            ) : displayTasks.length === 0 ? (
              <div className={styles.empty}>{t("taskMonitor.noTasks")}</div>
            ) : (
              displayTasks.map((task) => (
                <div
                  key={task.task_id}
                  className={`${styles.taskCard} ${
                    selectedId === task.task_id ? styles.taskCardSelected : ""
                  }`}
                  onClick={() => setSelectedId(task.task_id)}
                >
                  <div className={styles.taskCardHeader}>
                    <span className={styles.taskCardTitle}>
                      {task.title || "—"}
                    </span>
                    <span className={styles.cardActions}>
                      {statusTag(task.status, t)}
                      {canCancelTask(task) && (
                        <Button
                          size="small"
                          type="text"
                          danger
                          icon={<StopOutlined />}
                          loading={cancellingId === task.task_id}
                          onClick={(e) => {
                            e.stopPropagation();
                            cancelTask(task.task_id);
                          }}
                          title={t("taskMonitor.cancel")}
                        />
                      )}
                    </span>
                  </div>
                  <div className={styles.taskCardMeta}>
                    {task.tool_name && (
                      <span className={styles.taskCardTool}>
                        {task.tool_name}
                      </span>
                    )}
                    {task.current_stage && (
                      <span className={styles.taskCardStage}>
                        {task.current_stage}
                      </span>
                    )}
                  </div>
                  <div className={styles.taskCardFooter}>
                    <span className={styles.taskCardTime}>
                      {formatTimestamp(task.created_at)}
                    </span>
                    <span className={styles.taskCardDuration}>
                      {formatDuration(task.created_at, task.finished_at)}
                    </span>
                  </div>
                  {task.progress != null && task.progress > 0 && (
                    <div className={styles.progressBar}>
                      <div
                        className={styles.progressFill}
                        style={{ width: `${Math.min(100, task.progress)}%` }}
                      />
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Right: task detail */}
        <div className={styles.detailPanel}>
          {!selectedId ? (
            <div className={styles.empty}>{t("taskMonitor.selectTask")}</div>
          ) : detailLoading && !detail ? (
            <div className={styles.empty}>
              <Spin />
            </div>
          ) : detail ? (
            <div className={styles.detailScroll}>
              {/* Header */}
              <div className={styles.detailHeader}>
                <h2 className={styles.detailTitle}>{detail.title}</h2>
                <div className={styles.detailStatus}>
                  {statusTag(detail.status, t)}
                  {canCancelTask(detail) && (
                    <Button
                      size="small"
                      danger
                      icon={<StopOutlined />}
                      loading={cancellingId === detail.task_id}
                      onClick={() => cancelTask(detail.task_id)}
                    >
                      {t("taskMonitor.cancel")}
                    </Button>
                  )}
                </div>
              </div>

              {/* Info grid */}
              <div className={styles.detailGrid}>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>task_id</span>
                  <code className={styles.detailValue}>{detail.task_id}</code>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>session_id</span>
                  <code className={styles.detailValue}>
                    {detail.session_id}
                  </code>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>
                    {t("taskMonitor.source")}
                  </span>
                  <span className={styles.detailValue}>{detail.source}</span>
                </div>
                {detail.tool_name && (
                  <div className={styles.detailField}>
                    <span className={styles.detailLabel}>
                      {t("taskMonitor.toolName")}
                    </span>
                    <span className={styles.detailValue}>
                      {detail.tool_name}
                    </span>
                  </div>
                )}
                {detail.agent_id && (
                  <div className={styles.detailField}>
                    <span className={styles.detailLabel}>
                      {t("taskMonitor.agent")}
                    </span>
                    <code className={styles.detailValue}>
                      {detail.agent_id}
                    </code>
                  </div>
                )}
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>
                    {t("taskMonitor.duration")}
                  </span>
                  <span className={styles.detailValue}>
                    {formatDuration(detail.created_at, detail.finished_at)}
                  </span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>
                    {t("taskMonitor.created")}
                  </span>
                  <span className={styles.detailValue}>
                    {formatTimestamp(detail.created_at)}
                  </span>
                </div>
                {detail.finished_at && (
                  <div className={styles.detailField}>
                    <span className={styles.detailLabel}>
                      {t("taskMonitor.finished")}
                    </span>
                    <span className={styles.detailValue}>
                      {formatTimestamp(detail.finished_at)}
                    </span>
                  </div>
                )}
              </div>

              {/* Metadata */}
              {detail.metadata && Object.keys(detail.metadata).length > 0 && (
                <div className={styles.detailSection}>
                  <h3 className={styles.sectionTitle}>
                    {t("taskMonitor.metadata")}
                  </h3>
                  <pre className={styles.preBlock}>
                    {JSON.stringify(detail.metadata, null, 2)}
                  </pre>
                </div>
              )}

              {/* Error */}
              {detail.error && (
                <div className={styles.errorBlock}>{detail.error}</div>
              )}

              {/* Result summary */}
              {detail.result_summary && (
                <div className={styles.resultBlock}>
                  {detail.result_summary}
                </div>
              )}

              {/* Events timeline */}
              {detail.events.length > 0 && (
                <div className={styles.detailSection}>
                  <h3 className={styles.sectionTitle}>
                    {t("taskMonitor.events")} ({detail.events.length})
                  </h3>
                  <div className={styles.timeline}>
                    {detail.events.map((evt, i) => (
                      <div key={i} className={styles.timelineItem}>
                        <div
                          className={`${styles.timelineDot} ${
                            evt.event_type === "error" ||
                            evt.event_type === "task_failed"
                              ? styles.timelineDotError
                              : evt.event_type === "stage_completed" ||
                                evt.event_type === "task_done"
                              ? styles.timelineDotSuccess
                              : evt.event_type === "task_cancelled"
                              ? styles.timelineDotCancelled
                              : ""
                          }`}
                        >
                          {EVENT_ICON[evt.event_type] ?? "•"}
                        </div>
                        <div className={styles.timelineContent}>
                          <div className={styles.timelineHeader}>
                            <span className={styles.timelineType}>
                              {evt.event_type}
                            </span>
                            <span className={styles.timelineTime}>
                              {formatTimestamp(evt.timestamp)}
                            </span>
                          </div>
                          <div className={styles.timelineMessage}>
                            {evt.message}
                          </div>
                          {evt.stage && (
                            <div className={styles.timelineStage}>
                              stage: {evt.stage}
                            </div>
                          )}
                          {evt.agent_id && (
                            <div className={styles.timelineAgent}>
                              agent: {evt.agent_id}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
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
