import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  Pagination,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";

import { PageHeader } from "@/components/PageHeader";
import { useAppMessage } from "../../../hooks/useAppMessage";
import { useIsAdmin } from "../../../hooks/useIsAdmin";
import {
  adminSessionsApi,
  classifyError,
  type AdminSessionDetailResponse,
  type AdminSessionSummary,
} from "../../../api/modules/adminSessions";
import styles from "./index.module.less";

const { Text } = Typography;

const DEFAULT_LIMIT = 25;
const DETAIL_LAST_N = 200;

type Filters = {
  q: string;
  userId: string;
  channel: string;
};

function formatDate(raw?: string): string {
  if (!raw) return "—";
  const d = dayjs(raw);
  return d.isValid() ? d.format("YYYY-MM-DD HH:mm") : raw;
}

function messageToText(content: unknown): string {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object") {
          const b = block as Record<string, unknown>;
          if (typeof b.text === "string") return b.text;
          if (typeof b.content === "string") return b.content;
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  if (typeof content === "object") {
    const b = content as Record<string, unknown>;
    if (typeof b.text === "string") return b.text;
    if (typeof b.content === "string") return b.content;
    try {
      return JSON.stringify(content);
    } catch {
      return "";
    }
  }
  return String(content);
}

function AdminSessionsPage() {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const {
    status: adminStatus,
    refetch: refetchAdmin,
    errorKind,
  } = useIsAdmin();

  const [rows, setRows] = useState<AdminSessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_LIMIT);
  const [filters, setFilters] = useState<Filters>({
    q: "",
    userId: "",
    channel: "",
  });
  // Working-copy inputs so typing does not re-fetch on every keystroke;
  // applied by the "Apply" button or Enter.
  const [qInput, setQInput] = useState("");
  const [userIdInput, setUserIdInput] = useState("");
  const [channelInput, setChannelInput] = useState("");

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<AdminSessionDetailResponse | null>(null);

  const load = useCallback(
    async (nextPage: number, nextPageSize: number, nextFilters: Filters) => {
      setLoading(true);
      try {
        const resp = await adminSessionsApi.list({
          q: nextFilters.q || undefined,
          userId: nextFilters.userId || undefined,
          channel: nextFilters.channel || undefined,
          limit: nextPageSize,
          offset: (nextPage - 1) * nextPageSize,
        });
        setRows(resp.sessions);
        setTotal(resp.total);
      } catch (err) {
        const e = classifyError(err);
        if (e.kind === "forbidden") {
          // Role was revoked mid-session; ask the gate hook to re-probe.
          refetchAdmin();
          return;
        }
        message.error(
          t("adminSessions.loadFailed", {
            defaultValue: "Failed to load sessions",
          }) + (e.message ? `: ${e.message.slice(0, 120)}` : ""),
        );
      } finally {
        setLoading(false);
      }
    },
    [message, refetchAdmin, t],
  );

  // Initial + filter/page-driven fetch, gated on admin probe success.
  useEffect(() => {
    if (adminStatus !== "admin") return;
    load(page, pageSize, filters);
  }, [adminStatus, page, pageSize, filters, load]);

  const applyFilters = useCallback(() => {
    setPage(1);
    setFilters({
      q: qInput.trim(),
      userId: userIdInput.trim(),
      channel: channelInput.trim(),
    });
  }, [qInput, userIdInput, channelInput]);

  const resetFilters = useCallback(() => {
    setQInput("");
    setUserIdInput("");
    setChannelInput("");
    setPage(1);
    setFilters({ q: "", userId: "", channel: "" });
  }, []);

  const openDetail = useCallback(
    async (sessionId: string) => {
      setDetailOpen(true);
      setDetail(null);
      setDetailLoading(true);
      try {
        const d = await adminSessionsApi.get(sessionId, DETAIL_LAST_N);
        setDetail(d);
      } catch (err) {
        const e = classifyError(err);
        if (e.kind === "not_found") {
          message.error(
            t("adminSessions.sessionNotFound", {
              defaultValue: "Session not found",
            }),
          );
        } else if (e.kind === "forbidden") {
          refetchAdmin();
        } else {
          message.error(
            t("adminSessions.detailFailed", {
              defaultValue: "Failed to load session detail",
            }),
          );
        }
        setDetailOpen(false);
      } finally {
        setDetailLoading(false);
      }
    },
    [message, refetchAdmin, t],
  );

  const columns = useMemo(
    () => [
      {
        title: t("adminSessions.columns.sessionId", {
          defaultValue: "Session ID",
        }),
        dataIndex: "session_id",
        key: "session_id",
        width: 360,
        render: (value: string) => (
          // Deliberately NOT using antd's `<Text code>` here: in dark mode
          // the inner <code> inherits a muted light-theme color from antd's
          // Typography root that our generic table overrides can't reach,
          // leaving the id unreadable. Render a plain styled <code> and let
          // our own .sessionId class handle theming.
          <Text copyable={{ text: value }}>
            <code className={styles.sessionId}>{value}</code>
          </Text>
        ),
      },
      {
        title: t("adminSessions.columns.title", { defaultValue: "Title" }),
        dataIndex: "title",
        key: "title",
        render: (value: string, row: AdminSessionSummary) => (
          <a onClick={() => openDetail(row.session_id)}>{value || "—"}</a>
        ),
      },
      {
        title: t("adminSessions.columns.user", { defaultValue: "User" }),
        dataIndex: "user_id",
        key: "user_id",
        width: 140,
        render: (value?: string) => value || <Text type="secondary">—</Text>,
      },
      {
        title: t("adminSessions.columns.agent", { defaultValue: "Agent" }),
        dataIndex: "agent_id",
        key: "agent_id",
        width: 140,
        render: (value: string | undefined, row: AdminSessionSummary) =>
          value || row.agent || <Text type="secondary">—</Text>,
      },
      {
        title: t("adminSessions.columns.channel", {
          defaultValue: "Channel",
        }),
        dataIndex: "channel",
        key: "channel",
        width: 110,
        render: (value?: string) =>
          value ? <Tag>{value}</Tag> : <Text type="secondary">—</Text>,
      },
      {
        title: t("adminSessions.columns.started", { defaultValue: "Started" }),
        dataIndex: "started",
        key: "started",
        width: 170,
        render: (value?: string) => formatDate(value),
      },
      {
        title: t("adminSessions.columns.messages", {
          defaultValue: "Msgs",
        }),
        dataIndex: "msg_count",
        key: "msg_count",
        width: 80,
        align: "right" as const,
        render: (v?: number) => v ?? 0,
      },
      {
        title: t("adminSessions.columns.actions", {
          defaultValue: "Actions",
        }),
        key: "actions",
        width: 100,
        render: (_: unknown, row: AdminSessionSummary) => (
          <Button type="link" onClick={() => openDetail(row.session_id)}>
            {t("adminSessions.view", { defaultValue: "View" })}
          </Button>
        ),
      },
    ],
    [openDetail, t],
  );

  // ─── Gate states ────────────────────────────────────────────────────────

  if (adminStatus === "loading") {
    return (
      <div className={styles.container}>
        <Spin />
      </div>
    );
  }

  if (adminStatus === "denied") {
    return (
      <div className={styles.container}>
        <div className={styles.forbiddenBanner}>
          <Typography.Title level={4}>
            {t("adminSessions.denied.title", {
              defaultValue: "Admin access required",
            })}
          </Typography.Title>
          <Text type="secondary">
            {t("adminSessions.denied.description", {
              defaultValue:
                "This page is only visible to administrators. If you believe this is a mistake, ask an admin to grant you the `admin` role.",
            })}
          </Text>
        </div>
      </div>
    );
  }

  if (adminStatus === "error") {
    return (
      <div className={styles.container}>
        <div className={styles.errorBanner}>
          <Typography.Title level={4}>
            {t("adminSessions.error.title", {
              defaultValue: "Could not reach admin API",
            })}
          </Typography.Title>
          <Text type="secondary">
            {t("adminSessions.error.description", {
              defaultValue:
                "Something went wrong while checking your permissions. Please retry.",
            })}
            {errorKind ? ` (${errorKind})` : ""}
          </Text>
          <div style={{ marginTop: 16 }}>
            <Button type="primary" onClick={refetchAdmin}>
              {t("adminSessions.error.retry", { defaultValue: "Retry" })}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // ─── Main ───────────────────────────────────────────────────────────────

  return (
    <div className={styles.container}>
      <PageHeader
        parent={t("nav.adminGroup", { defaultValue: "Admin" })}
        current={t("adminSessions.title", { defaultValue: "All sessions" })}
      />

      <div className={styles.filterBar}>
        <Input.Search
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          onSearch={applyFilters}
          placeholder={t("adminSessions.filters.qPlaceholder", {
            defaultValue: "Search title / tags / topics",
          })}
          allowClear
          style={{ maxWidth: 280 }}
        />
        <Input
          value={userIdInput}
          onChange={(e) => setUserIdInput(e.target.value)}
          onPressEnter={applyFilters}
          placeholder={t("adminSessions.filters.userPlaceholder", {
            defaultValue: "Filter by user id",
          })}
          allowClear
          style={{ maxWidth: 200 }}
        />
        <Input
          value={channelInput}
          onChange={(e) => setChannelInput(e.target.value)}
          onPressEnter={applyFilters}
          placeholder={t("adminSessions.filters.channelPlaceholder", {
            defaultValue: "Filter by channel",
          })}
          allowClear
          style={{ maxWidth: 180 }}
        />
        <Button type="primary" onClick={applyFilters}>
          {t("adminSessions.filters.apply", { defaultValue: "Apply" })}
        </Button>
        <Button onClick={resetFilters}>
          {t("adminSessions.filters.reset", { defaultValue: "Reset" })}
        </Button>
        <Button onClick={() => load(page, pageSize, filters)} loading={loading}>
          {t("adminSessions.filters.refresh", { defaultValue: "Refresh" })}
        </Button>
      </div>

      <Card className={styles.tableCard} bordered>
        <Table<AdminSessionSummary>
          rowKey="session_id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={false}
          size="small"
          scroll={{ y: "calc(100vh - 340px)" }}
          locale={{
            emptyText: loading ? (
              <Spin />
            ) : (
              <Empty
                description={t("adminSessions.empty", {
                  defaultValue: "No sessions match the current filters",
                })}
              />
            ),
          }}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            paddingTop: 8,
          }}
        >
          <Pagination
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            pageSizeOptions={[10, 25, 50, 100]}
            onChange={(nextPage, nextPageSize) => {
              setPage(nextPage);
              setPageSize(nextPageSize);
            }}
            showTotal={(t_) =>
              t("adminSessions.totalCount", {
                defaultValue: "{{count}} sessions",
                count: t_,
              })
            }
          />
        </div>
      </Card>

      <Drawer
        open={detailOpen}
        width={720}
        onClose={() => setDetailOpen(false)}
        title={
          detail ? (
            <div className={styles.drawerHeader}>
              <Text strong>
                {((detail.metadata as Record<string, unknown>)
                  .title as string) || detail.session_id}
              </Text>
              <Text copyable={{ text: detail.session_id }}>
                <code className={styles.sessionId}>{detail.session_id}</code>
              </Text>
            </div>
          ) : (
            t("adminSessions.detail.title", { defaultValue: "Session detail" })
          )
        }
        destroyOnClose
      >
        {detailLoading ? (
          <div style={{ textAlign: "center", padding: 48 }}>
            <Spin />
          </div>
        ) : detail ? (
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <div>
              <Typography.Title level={5}>
                {t("adminSessions.detail.metadata", {
                  defaultValue: "Metadata",
                })}
              </Typography.Title>
              <dl className={styles.metadataGrid}>
                <dt>user_id</dt>
                <dd>
                  {(detail.metadata.user_id as string | undefined) || "—"}
                </dd>
                <dt>agent_id</dt>
                <dd>
                  {(detail.metadata.agent_id as string | undefined) ||
                    (detail.metadata.agent as string | undefined) ||
                    "—"}
                </dd>
                <dt>channel</dt>
                <dd>
                  {(detail.metadata.channel as string | undefined) || "—"}
                </dd>
                <dt>started</dt>
                <dd>
                  {formatDate(
                    (detail.metadata.started_at as string | undefined) ||
                      (detail.metadata.started as string | undefined),
                  )}
                </dd>
                <dt>message_count</dt>
                <dd>{detail.total_messages}</dd>
                {Array.isArray(detail.metadata.tags) &&
                (detail.metadata.tags as string[]).length > 0 ? (
                  <>
                    <dt>tags</dt>
                    <dd>
                      {(detail.metadata.tags as string[]).map((tag) => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                    </dd>
                  </>
                ) : null}
              </dl>
            </div>

            <div>
              <Typography.Title level={5}>
                {t("adminSessions.detail.messages", {
                  defaultValue: "Messages",
                })}
                {detail.truncated ? (
                  <Text type="warning" style={{ marginLeft: 8, fontSize: 12 }}>
                    {t("adminSessions.detail.truncated", {
                      defaultValue:
                        "Showing last {{shown}} of {{total}} — use the API directly for full export",
                      shown: detail.messages.length,
                      total: detail.total_messages,
                    })}
                  </Text>
                ) : null}
              </Typography.Title>
              <div className={styles.messageList}>
                {detail.messages.length === 0 ? (
                  <Empty
                    description={t("adminSessions.detail.noMessages", {
                      defaultValue: "No messages in this session",
                    })}
                  />
                ) : (
                  detail.messages.map((msg, idx) => {
                    const role = (msg.role as string) || "unknown";
                    const text = messageToText(msg.content);
                    const ts = msg.timestamp as string | undefined;
                    return (
                      <div
                        key={idx}
                        className={`${styles.messageItem} ${
                          role === "user"
                            ? styles.user
                            : role === "assistant"
                            ? styles.assistant
                            : ""
                        }`}
                      >
                        <span className={styles.role}>{role}</span>
                        {ts ? (
                          <span className={styles.timestamp}>
                            {formatDate(ts)}
                          </span>
                        ) : null}
                        <div className={styles.body}>{text || "—"}</div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </Space>
        ) : null}
      </Drawer>
    </div>
  );
}

export default AdminSessionsPage;
