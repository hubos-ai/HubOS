/**
 * Slash command definitions for HubOS chat.
 *
 * Each command carries a stable `id` and an `i18nKey` prefix. Descriptions,
 * categories, and usage hints are resolved at render time via i18n so the
 * menu follows the system language automatically.
 */
export interface SlashCommand {
  /** Stable machine-readable id, e.g. "stop" */
  id: string;
  /** Full slash command string, e.g. "/stop" */
  command: string;
  /** i18n key prefix: `${i18nKey}.description` etc. */
  i18nKey: string;
  /** e.g. "/compact <instruction>" — shown as usage hint */
  usage?: string;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  // ── Task control ────────────────────────────────────────────
  { id: "stop", command: "/stop", i18nKey: "chat.slashCommands.stop" },
  {
    id: "approve",
    command: "/approve",
    i18nKey: "chat.slashCommands.approve",
  },
  { id: "deny", command: "/deny", i18nKey: "chat.slashCommands.deny" },
  {
    id: "restart",
    command: "/restart",
    i18nKey: "chat.slashCommands.restart",
  },

  // ── Context ─────────────────────────────────────────────────
  {
    id: "clear",
    command: "/clear",
    i18nKey: "chat.slashCommands.clear",
  },
  {
    id: "compact",
    command: "/compact",
    i18nKey: "chat.slashCommands.compact",
    usage: "/compact <instruction>",
  },
  {
    id: "compact_str",
    command: "/compact_str",
    i18nKey: "chat.slashCommands.compact_str",
  },
  {
    id: "history",
    command: "/history",
    i18nKey: "chat.slashCommands.history",
  },
  {
    id: "await_summary",
    command: "/await_summary",
    i18nKey: "chat.slashCommands.await_summary",
  },
  {
    id: "long_term_memory",
    command: "/long_term_memory",
    i18nKey: "chat.slashCommands.long_term_memory",
  },

  // ── Chat ────────────────────────────────────────────────────
  { id: "new", command: "/new", i18nKey: "chat.slashCommands.new" },
  {
    id: "message",
    command: "/message",
    i18nKey: "chat.slashCommands.message",
    usage: "/message <index>",
  },

  // ── Debug ───────────────────────────────────────────────────
  {
    id: "dump_history",
    command: "/dump_history",
    i18nKey: "chat.slashCommands.dump_history",
  },
  {
    id: "load_history",
    command: "/load_history",
    i18nKey: "chat.slashCommands.load_history",
  },
  {
    id: "status",
    command: "/status",
    i18nKey: "chat.slashCommands.status",
  },
  {
    id: "reload-config",
    command: "/reload-config",
    i18nKey: "chat.slashCommands.reloadConfig",
  },
  { id: "version", command: "/version", i18nKey: "chat.slashCommands.version" },
  { id: "logs", command: "/logs", i18nKey: "chat.slashCommands.logs" },

  // ── Daemon ──────────────────────────────────────────────────
  {
    id: "daemon status",
    command: "/daemon status",
    i18nKey: "chat.slashCommands.daemonStatus",
  },
  {
    id: "daemon restart",
    command: "/daemon restart",
    i18nKey: "chat.slashCommands.daemonRestart",
  },
  {
    id: "daemon reload-config",
    command: "/daemon reload-config",
    i18nKey: "chat.slashCommands.daemonReloadConfig",
  },
  {
    id: "daemon version",
    command: "/daemon version",
    i18nKey: "chat.slashCommands.daemonVersion",
  },
  {
    id: "daemon logs",
    command: "/daemon logs",
    i18nKey: "chat.slashCommands.daemonLogs",
  },
  {
    id: "daemon approve",
    command: "/daemon approve",
    i18nKey: "chat.slashCommands.daemonApprove",
  },
];
