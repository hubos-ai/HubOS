// -*- coding: utf-8 -*-
/**
 * ChatTaskPanel — collapsible right panel showing task monitor data
 * for the current chat session.
 *
 * Uses existing taskMonitorApi + useTaskMonitorStream.
 * Applies local updates from SSE before re-fetching (debounced).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Tag, Drawer, Tooltip } from "antd";
import {
  ReloadOutlined,
  StopOutlined,
  BranchesOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { taskMonitorApi } from "@/api/modules/taskMonitor";
import { runControlApi } from "@/api/modules/runControl";
import type { RunEntry } from "@/api/modules/runControl";
import type { Task, TaskStatus } from "@/api/types/taskMonitor";
import { useTaskMonitorStream } from "@/pages/TaskMonitor/useTaskMonitorStream";
import styles from "./index.module.less";

// ── Status config (shared with TaskMonitor page) ──────────────────────────

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

function formatDuration(created: number, finished?: number): string {
  const end = finished || Date.now() / 1000;
  const diff = Math.max(0, Math.round(end - created));
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`;
  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  return `${h}h ${m}m`;
}

// ── Event icon ────────────────────────────────────────────────────────────

const EVENT_ICON: Record<string, string> = {
  task_created: "+",
  task_updated: "~",
  stage_started: "\u25B6",
  stage_completed: "\u2713",
  log: "\u2022",
  error: "!",
  task_done: "\u2713",
  task_failed: "\u2717",
  task_cancelled: "\u25A0",
};

function canCancelTask(task: Task): boolean {
  return (
    task.status === "pending" ||
    task.status === "running" ||
    task.status === "waiting"
  );
}

function isActiveTask(task: Task): boolean {
  return (
    task.status === "pending" ||
    task.status === "running" ||
    task.status === "waiting"
  );
}

const ACTIVE_RUN_STATUSES = new Set(["running", "pending", "waiting"]);

// ── Props ─────────────────────────────────────────────────────────────────

export interface ChatTaskPanelProps {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}

// ── Component ─────────────────────────────────────────────────────────────

export default function ChatTaskPanel({
  sessionId: _sessionId, // eslint-disable-line @typescript-eslint/no-unused-vars
  open,
  onClose,
}: ChatTaskPanelProps) {
  const { t } = useTranslation();

  // Detect mobile viewport for responsive rendering
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

  // SSE (only connected while panel is open, to avoid unnecessary connections)
  const { lastEvent, connected } = useTaskMonitorStream({ enabled: open });

  // Task list
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const fetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Expanded task detail
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // RunControl: active runs for this session
  const [runEntries, setRunEntries] = useState<RunEntry[]>([]);

  // ── Fetch tasks ────────────────────────────────────────────────────────

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    try {
      // Always fetch recent tasks without session filter — the session_id
      // stored by tools may differ from the chat URL id (e.g. console
      // channel uses internal ids).
      const res = await taskMonitorApi.listTasks({ limit: 30 });
      setTasks(res.tasks);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch RunControl entries for the session
  const fetchRuns = useCallback(async () => {
    try {
      if (_sessionId) {
        const res = await runControlApi.getActiveRuns(_sessionId);
        const sessionRuns = res.runs ?? [];
        if (sessionRuns.length > 0) {
          setRunEntries(sessionRuns);
          return;
        }
      }
      // Fallback: console/tool layers may disagree about the visible
      // AgentScope session id. Showing active runs across sessions is better
      // than an empty panel while the current chat is visibly executing.
      const all = await runControlApi.listRuns({ activeOnly: true });
      setRunEntries(all.runs ?? []);
    } catch {
      // silent
    }
  }, [_sessionId]);

  // ── Debounced SSE-driven refresh ───────────────────────────────────────

  useEffect(() => {
    if (!lastEvent) return;
    // Accept all SSE events — don't filter by session_id because the
    // tool-layer session_id may not match the chat URL id.
    // Debounce: coalesce rapid events
    if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current);
    fetchTimerRef.current = setTimeout(() => {
      fetchTasks();
      fetchRuns();
    }, 300);
    return () => {
      if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current);
    };
  }, [lastEvent, fetchTasks, fetchRuns]);

  // ── Initial load & when panel opens ────────────────────────────────────

  useEffect(() => {
    if (open) {
      fetchTasks();
      fetchRuns();
    }
  }, [open, fetchTasks, fetchRuns]);

  // RunControl has no SSE stream yet. Poll lightly while the panel is open so
  // plain chat runs show up even when no TaskMonitor event is emitted.
  // Use a ref for the callback to avoid restarting the interval on every
  // fetchRuns identity change.
  const fetchRunsRef = useRef(fetchRuns);
  fetchRunsRef.current = fetchRuns;

  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => {
      fetchRunsRef.current();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [open]);

  const refreshAll = useCallback(() => {
    fetchTasks();
    fetchRuns();
  }, [fetchTasks, fetchRuns]);

  const cancelTask = useCallback(
    async (taskId: string) => {
      setCancellingId(taskId);
      try {
        // Try RunControl cancel first if this task has a matching run
        const run = runEntries.find((r) => r.monitor_task_id === taskId);
        if (run && run.cancellable) {
          await runControlApi.cancelRun(run.run_id);
        } else {
          await taskMonitorApi.cancelTask(taskId);
        }
        await fetchTasks();
        await fetchRuns();
      } catch {
        // Keep the panel lightweight; the next SSE/list refresh will show truth.
      } finally {
        setCancellingId(null);
      }
    },
    [fetchTasks, fetchRuns, runEntries],
  );

  // Find run entry for a given task
  const getRunForTask = useCallback(
    (taskId: string): RunEntry | undefined =>
      runEntries.find((r) => r.monitor_task_id === taskId),
    [runEntries],
  );

  // ── Stats ──────────────────────────────────────────────────────────────

  const stats = useMemo(() => {
    const s = { running: 0, waiting: 0, pending: 0 };
    for (const t of tasks.filter(isActiveTask)) {
      if (t.status === "running") s.running++;
      else if (t.status === "waiting") s.waiting++;
      else if (t.status === "pending") s.pending++;
    }
    // Also count RunControl runs that aren't tracked by TaskMonitor
    const matchedTaskIds = new Set(
      tasks.filter(isActiveTask).map((t) => t.task_id),
    );
    for (const r of runEntries) {
      if (!ACTIVE_RUN_STATUSES.has(r.status)) continue;
      if (r.monitor_task_id && matchedTaskIds.has(r.monitor_task_id)) continue;
      const st = r.status as string;
      if (st === "running") s.running++;
      else if (st === "waiting") s.waiting++;
      else s.pending++;
    }
    return s;
  }, [tasks, runEntries]);

  const activeTasks = useMemo(() => tasks.filter(isActiveTask), [tasks]);

  // RunControl runs not backed by a TaskMonitor task
  const unmatchedRuns = useMemo(() => {
    // Prefer showing a duplicate over showing an empty panel while HubOS is
    // visibly running. TaskMonitor and RunControl are separate layers, and
    // some plain chat/tool runs have no monitor_task_id.
    return runEntries.filter((r) => ACTIVE_RUN_STATUSES.has(r.status));
  }, [runEntries]);

  const cancelRunEntry = useCallback(
    async (runId: string) => {
      setCancellingId(runId);
      try {
        await runControlApi.cancelRun(runId);
        await fetchTasks();
        await fetchRuns();
      } catch {
        // silent
      } finally {
        setCancellingId(null);
      }
    },
    [fetchTasks, fetchRuns],
  );

  const RUN_TYPE_LABELS: Record<string, string> = {
    chat: "chat",
    spawn: "spawn",
    workflow: "workflow",
    delegate: "delegate",
    plan: "plan",
  };

  // ── Panel content ──────────────────────────────────────────────────────

  const content = (
    <div className={styles.panel}>
      {/* Connection indicator */}
      <div className={styles.panelHeader}>
        <span className={styles.panelTitle}>{t("chatTask.panelTitle")}</span>
        <span className={connected ? styles.dotOnline : styles.dotOffline}>
          {connected
            ? t("taskMonitor.connected")
            : t("taskMonitor.disconnected")}
        </span>
      </div>

      {/* Stats bar */}
      <div className={styles.statsBar}>
        {stats.pending > 0 && (
          <span className={styles.stat} data-status="pending">
            {stats.pending} {t("taskMonitor.statusPending")}
          </span>
        )}
        {stats.running > 0 && (
          <span className={styles.stat} data-status="running">
            {stats.running} {t("taskMonitor.statusRunning")}
          </span>
        )}
        {stats.waiting > 0 && (
          <span className={styles.stat} data-status="waiting">
            {stats.waiting} {t("taskMonitor.statusWaiting")}
          </span>
        )}
      </div>

      {/* Refresh */}
      <div className={styles.toolbar}>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={refreshAll}
          loading={loading}
        >
          {t("taskMonitor.refresh")}
        </Button>
      </div>

      {/* Task list */}
      <div className={styles.taskList}>
        {activeTasks.length === 0 && unmatchedRuns.length === 0 ? (
          <div className={styles.empty}>{t("taskMonitor.noTasks")}</div>
        ) : (
          <>
            {activeTasks.map((task) => {
              const runInfo = getRunForTask(task.task_id);
              return (
                <div key={task.task_id} className={styles.taskCard}>
                  <div
                    className={styles.taskCardHeader}
                    onClick={() =>
                      setExpandedId(
                        expandedId === task.task_id ? null : task.task_id,
                      )
                    }
                  >
                    <span className={styles.taskCardTitle}>
                      {runInfo && (
                        <Tooltip title={`run: ${runInfo.run_type}`}>
                          <BranchesOutlined
                            style={{
                              marginRight: 4,
                              fontSize: 11,
                              opacity: 0.5,
                            }}
                          />
                        </Tooltip>
                      )}
                      {task.title || "\u2014"}
                    </span>
                    <span className={styles.cardActions}>
                      {statusTag(task.status, t)}
                      {runInfo && !runInfo.cancellable && (
                        <Tooltip title={t("taskMonitor.markOnly", "Mark only")}>
                          <Tag color="default" style={{ fontSize: 9 }}>
                            {t("taskMonitor.markOnly", "mark_only")}
                          </Tag>
                        </Tooltip>
                      )}
                      {canCancelTask(task) &&
                        (!runInfo || runInfo.cancellable) && (
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
                  <div
                    className={styles.taskCardMeta}
                    onClick={() =>
                      setExpandedId(
                        expandedId === task.task_id ? null : task.task_id,
                      )
                    }
                  >
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
                  <div
                    className={styles.taskCardFooter}
                    onClick={() =>
                      setExpandedId(
                        expandedId === task.task_id ? null : task.task_id,
                      )
                    }
                  >
                    <span className={styles.taskCardDuration}>
                      {formatDuration(task.created_at, task.finished_at)}
                    </span>
                  </div>
                  {task.progress != null && task.progress > 0 && (
                    <div className={styles.progressBar}>
                      <div
                        className={styles.progressFill}
                        style={{
                          width: `${Math.min(100, task.progress)}%`,
                        }}
                      />
                    </div>
                  )}

                  {/* Expanded events */}
                  {expandedId === task.task_id && task.events.length > 0 && (
                    <div className={styles.eventsSection}>
                      {task.error && (
                        <div className={styles.errorLine}>{task.error}</div>
                      )}
                      {task.result_summary && (
                        <div className={styles.resultLine}>
                          {task.result_summary}
                        </div>
                      )}
                      <div className={styles.timeline}>
                        {task.events.map((evt, i) => (
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
                              {EVENT_ICON[evt.event_type] ?? "\u2022"}
                            </div>
                            <div className={styles.timelineContent}>
                              <div className={styles.timelineHeader}>
                                <span className={styles.timelineType}>
                                  {evt.event_type}
                                </span>
                              </div>
                              <div className={styles.timelineMessage}>
                                {evt.message}
                              </div>
                              {evt.stage && (
                                <div className={styles.timelineStage}>
                                  {evt.stage}
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {unmatchedRuns.map((run) => (
              <div key={run.run_id} className={styles.taskCard}>
                <div className={styles.taskCardHeader}>
                  <span className={styles.taskCardTitle}>
                    <BranchesOutlined
                      style={{ marginRight: 4, fontSize: 11, opacity: 0.5 }}
                    />
                    {run.run_type === "chat"
                      ? t("chatTask.chatRun", "聊天任务")
                      : `${RUN_TYPE_LABELS[run.run_type] ?? run.run_type} run`}
                  </span>
                  <span className={styles.cardActions}>
                    <Tag color="blue">{run.status}</Tag>
                    {!run.cancellable && (
                      <Tooltip title={t("taskMonitor.markOnly", "Mark only")}>
                        <Tag color="default" style={{ fontSize: 9 }}>
                          {t("taskMonitor.markOnly", "mark_only")}
                        </Tag>
                      </Tooltip>
                    )}
                    {run.cancellable && (
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<StopOutlined />}
                        loading={cancellingId === run.run_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          cancelRunEntry(run.run_id);
                        }}
                        title={t("taskMonitor.cancel")}
                      />
                    )}
                  </span>
                </div>
                <div className={styles.taskCardFooter}>
                  <span className={styles.taskCardDuration}>
                    {formatDuration(run.created_at)}
                  </span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );

  // ── Responsive: Drawer on mobile, inline panel on desktop ──────────────

  if (isMobile) {
    return (
      <Drawer
        title={t("chatTask.panelTitle")}
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
          title={t("chatTask.close")}
        >
          {"\u2715"}
        </button>
        {content}
      </div>
    </div>
  );
}
