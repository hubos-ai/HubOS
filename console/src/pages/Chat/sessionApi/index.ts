import {
  IAgentScopeRuntimeWebUISession,
  IAgentScopeRuntimeWebUISessionAPI,
  IAgentScopeRuntimeWebUIMessage,
} from "@agentscope-ai/chat";
import api, {
  type ChatSpec,
  type ChatHistory,
  type ChatStatus,
  type Message,
} from "../../../api";
import { toDisplayUrl } from "../utils";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_USER_ID = "default";
const DEFAULT_CHANNEL = "console";
const DEFAULT_SESSION_NAME = "New Chat";
const ROLE_TOOL = "tool";
const ROLE_USER = "user";
const ROLE_ASSISTANT = "assistant";
const ROLE_SYSTEM = "system";
const TYPE_PLUGIN_CALL_OUTPUT = "plugin_call_output";
const TYPE_MESSAGE = "message";

// LangChain BaseMessage discriminators emitted by XClaw / LangGraph history
// payloads. They must be mapped to AgentScope runtime message types before
// rendering, otherwise @agentscope-ai/chat warns "Unknown message type: ..."
// and falls back to rendering JSON blobs.
const LC_TYPE_HUMAN = "human";
const LC_TYPE_AI = "ai";
const LC_TYPE_SYSTEM = "system";
const LC_TYPE_TOOL = "tool";

// AgentScope message types the @agentscope-ai/chat Card component knows how
// to render. Keep this in sync with AgentScopeRuntimeMessageType in the
// chat library's types.tsx.
const AGENTSCOPE_KNOWN_TYPES = new Set([
  "message",
  "reasoning",
  "plugin_call",
  "plugin_call_output",
  "function_call",
  "function_call_output",
  "component_call",
  "component_call_output",
  "mcp_list_tools",
  "mcp_approval_request",
  "mcp_approval_response",
  "mcp_call",
  "mcp_call_output",
  "heartbeat",
  "error",
]);
// const CARD_REQUEST = "AgentScopeRuntimeRequestCard";
const CARD_RESPONSE = "AgentScopeRuntimeResponseCard";

// ---------------------------------------------------------------------------
// Window globals
// ---------------------------------------------------------------------------

interface CustomWindow extends Window {
  currentSessionId?: string;
  currentUserId?: string;
  currentChannel?: string;
}

declare const window: CustomWindow;

// ---------------------------------------------------------------------------
// Local helper types
// ---------------------------------------------------------------------------

/** A single item inside a message's content array. */
interface ContentItem {
  type: string;
  text?: string;
  [key: string]: unknown;
}

/** A backend message after role-normalisation (output of toOutputMessage). */
interface OutputMessage extends Omit<Message, "role"> {
  role: string;
  metadata: null;
  sequence_number?: number;
}

/**
 * Extended session carrying extra fields that the library type does not define
 * but our backend / window globals require.
 */
interface ExtendedSession extends IAgentScopeRuntimeWebUISession {
  /** Session identifier (channel:user_id format) */
  sessionId: string;
  /** User identifier */
  userId: string;
  /** Channel name */
  channel: string;
  /** Additional metadata */
  meta: Record<string, unknown>;
  /** Real backend UUID, used when id is overridden with a local timestamp. */
  realId?: string;
  /** Conversation status from backend. */
  status?: ChatStatus;
  /** ISO 8601 creation timestamp from backend. */
  createdAt?: string | null;
  /** Whether the backend is still generating a response for this session. */
  generating?: boolean;
}

// ---------------------------------------------------------------------------
// Message conversion helpers: backend flat messages → card-based UI format
// ---------------------------------------------------------------------------

/** Returns true if the error is a "Thread not found" 404 from the backend. */
function isThreadNotFoundError(err: unknown): boolean {
  if (err instanceof Error) {
    return (
      err.message.includes("Thread not found") || err.message.includes("404")
    );
  }
  return false;
}

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/** Extract plain text from a message's content array. */
const extractTextFromContent = (content: unknown): string => {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return String(content || "");
  return (content as ContentItem[])
    .filter((c) => c.type === "text")
    .map((c) => c.text || "")
    .filter(Boolean)
    .join("\n");
};

function resolveContentItemUrl(c: ContentItem): ContentItem {
  if (c.type === "image" && c.image_url) {
    return { ...c, image_url: toDisplayUrl(c.image_url as string) };
  }
  if (c.type === "audio" && c.data) {
    return { ...c, data: toDisplayUrl(c.data as string) };
  }
  if (c.type === "video" && c.video_url) {
    return { ...c, video_url: toDisplayUrl(c.video_url as string) };
  }
  if (c.type === "file" && (c.file_url || c.file_id)) {
    return {
      ...c,
      file_url: toDisplayUrl((c.file_url as string) || (c.file_id as string)),
      file_name: (c.filename as string) || (c.file_name as string) || "file",
    };
  }
  return c;
}

/** Map backend message content to request card content (text + image + file). */
function contentToRequestParts(
  content: unknown,
): Array<Record<string, unknown>> {
  if (typeof content === "string") {
    return [{ type: "text", text: content, status: "created" }];
  }
  if (!Array.isArray(content)) {
    return [{ type: "text", text: String(content || ""), status: "created" }];
  }
  const parts = (content as ContentItem[])
    .map(resolveContentItemUrl)
    .map((c) => ({ ...c, status: "created" }));

  if (parts.length === 0) {
    return [{ type: "text", text: "", status: "created" }];
  }

  return parts;
}
/**
 * Wrap legacy string content into the AgentScope IContent[] shape so the chat
 * Card's Message component can render it. LangChain BaseMessage payloads
 * frequently use a plain string content field; AgentScope expects an array
 * of typed parts (`{ type: "text", text, status: "completed" }`).
 */
function normalizeOutputMessageContent(
  content: unknown,
  msgType?: string,
): unknown {
  if (typeof content === "string") {
    return [{ type: "text", text: content, status: "completed" }];
  }
  if (!Array.isArray(content)) return content;
  // plugin_call / plugin_call_output carry structured DataContent with tool
  // call/response info (name, arguments, output).  Do NOT transform these —
  // the Card component expects the raw data shape for rendering.
  if (
    msgType === "plugin_call" ||
    msgType === "plugin_call_output" ||
    msgType === "function_call" ||
    msgType === "function_call_output"
  ) {
    return content;
  }
  return (content as ContentItem[]).map(resolveContentItemUrl);
}

/**
 * Resolve the effective role for a backend message, considering both
 * `role` and LangChain `type`. XClaw / LangGraph history may only carry
 * `type: "human" | "ai" | "system" | "tool"` without a corresponding role.
 */
function resolveMessageRole(msg: Message): string {
  const role = (msg.role as string) || "";
  // plugin_call_output always maps to "tool" so it renders inside the
  // response card alongside the corresponding plugin_call.
  if (msg.type === TYPE_PLUGIN_CALL_OUTPUT) {
    return ROLE_TOOL;
  }
  // plugin_call keeps its original role (usually "assistant") so the
  // response card groups it correctly with surrounding assistant messages.
  if (role) {
    return role;
  }
  switch (msg.type) {
    case LC_TYPE_HUMAN:
      return ROLE_USER;
    case LC_TYPE_AI:
      return ROLE_ASSISTANT;
    case LC_TYPE_TOOL:
      return ROLE_TOOL;
    case LC_TYPE_SYSTEM:
      return ROLE_SYSTEM;
    default:
      return role;
  }
}

/**
 * Map the backend message `type` field to one of the AgentScope runtime
 * message types the chat library recognises. LangChain BaseMessage
 * discriminators (human / ai / system / tool) are translated to AgentScope
 * equivalents; unknown types default to "message" so the card still renders
 * the text content instead of a JSON blob.
 */
function resolveMessageType(rawType: unknown, role: string): string {
  if (typeof rawType === "string") {
    if (AGENTSCOPE_KNOWN_TYPES.has(rawType)) return rawType;
    if (rawType === LC_TYPE_TOOL) return TYPE_PLUGIN_CALL_OUTPUT;
    if (
      rawType === LC_TYPE_HUMAN ||
      rawType === LC_TYPE_AI ||
      rawType === LC_TYPE_SYSTEM
    ) {
      return TYPE_MESSAGE;
    }
  }
  if (role === ROLE_TOOL) return TYPE_PLUGIN_CALL_OUTPUT;
  return TYPE_MESSAGE;
}

/**
 * Convert a backend message to a response output message in the shape the
 * AgentScope chat Card expects: known `role`, known `type`, AgentScope
 * `IContent[]` content, no LangChain metadata leaked through.
 */
const toOutputMessage = (msg: Message): OutputMessage => {
  const role = resolveMessageRole(msg);
  const type = resolveMessageType(msg.type, role);
  return {
    ...msg,
    role,
    type,
    metadata: null,
  };
};

/** Build a user card (AgentScopeRuntimeRequestCard) from a user message. */
function buildUserCard(msg: Message): IAgentScopeRuntimeWebUIMessage {
  const contentParts = contentToRequestParts(msg.content);
  return {
    id: (msg.id as string) || generateId(),
    role: "user",
    cards: [
      {
        code: "AgentScopeRuntimeRequestCard",
        data: {
          input: [
            {
              role: "user",
              type: "message",
              content: contentParts,
            },
          ],
        },
      },
    ],
  };
}

/**
 * Build an assistant response card (AgentScopeRuntimeResponseCard)
 * wrapping a group of consecutive non-user output messages.
 */
const buildResponseCard = (
  outputMessages: OutputMessage[],
): IAgentScopeRuntimeWebUIMessage => {
  const now = Math.floor(Date.now() / 1000);
  const maxSeq = outputMessages.reduce(
    (max, m) => Math.max(max, m.sequence_number || 0),
    0,
  );

  const normalizedMessages = outputMessages.map((msg) => ({
    ...msg,
    content: normalizeOutputMessageContent(
      msg.content,
      msg.type as string | undefined,
    ),
  }));

  return {
    id: generateId(),
    role: ROLE_ASSISTANT,
    cards: [
      {
        code: CARD_RESPONSE,
        data: {
          id: `response_${generateId()}`,
          output: normalizedMessages,
          object: "response",
          status: "completed",
          created_at: now,
          sequence_number: maxSeq + 1,
          error: null,
          completed_at: now,
          usage: null,
        },
      },
    ],
    msgStatus: "finished",
  };
};

/**
 * Convert flat backend messages into the card-based format expected by
 * the @agentscope-ai/chat component.
 *
 * - User messages → AgentScopeRuntimeRequestCard
 * - Consecutive non-user messages (assistant / system / tool) → grouped
 *   into a single AgentScopeRuntimeResponseCard with all output messages.
 *
 * Both `role` and LangChain `type` are inspected when classifying a message,
 * so XClaw histories that only carry `type: "human"` still group correctly.
 */
const convertMessages = (
  messages: Message[],
): IAgentScopeRuntimeWebUIMessage[] => {
  const isUserMessage = (msg: Message): boolean =>
    resolveMessageRole(msg) === ROLE_USER;

  // System messages (LangChain `system` type or role) are internal prompt
  // scaffolding and must never surface in the conversation timeline.
  const visible = messages.filter(
    (msg) => resolveMessageRole(msg) !== ROLE_SYSTEM,
  );

  const result: IAgentScopeRuntimeWebUIMessage[] = [];
  let i = 0;
  while (i < visible.length) {
    if (isUserMessage(visible[i])) {
      result.push(buildUserCard(visible[i++]));
    } else {
      const outputMsgs: OutputMessage[] = [];
      while (i < visible.length && !isUserMessage(visible[i])) {
        outputMsgs.push(toOutputMessage(visible[i++]));
      }
      if (outputMsgs.length) result.push(buildResponseCard(outputMsgs));
    }
  }

  return result;
};

const chatSpecToSession = (chat: ChatSpec): ExtendedSession =>
  ({
    id: chat.id,
    name: chat.name || DEFAULT_SESSION_NAME,
    sessionId: chat.session_id,
    userId: chat.user_id,
    channel: chat.channel,
    messages: [],
    meta: chat.meta || {},
    status: chat.status ?? "idle",
    createdAt: chat.created_at ?? null,
  }) as ExtendedSession;

/** Returns true when id is a pure numeric local timestamp (not a backend UUID). */
const isLocalTimestamp = (id: string): boolean => /^\d+$/.test(id);

/** Detect if backend is still generating content for this chat. */
const isGenerating = (chatHistory: ChatHistory): boolean => {
  if (chatHistory.status === "running") return true;
  if (chatHistory.status === "idle") return false;
  const msgs = chatHistory.messages || [];
  if (msgs.length === 0) return false;
  const last = msgs[msgs.length - 1];
  return last.role === ROLE_USER;
};

/**
 * Resolve and persist the real backend UUID for a local timestamp session.
 * Stores the real UUID as realId while keeping the timestamp as id, so the
 * library's internal currentSessionId (timestamp) remains valid.
 * Returns the resolved real UUID, or null if not found.
 */
const resolveRealId = (
  sessionList: IAgentScopeRuntimeWebUISession[],
  tempSessionId: string,
): { list: IAgentScopeRuntimeWebUISession[]; realId: string | null } => {
  const realSession = sessionList.find(
    (s) => (s as ExtendedSession).sessionId === tempSessionId,
  );
  if (!realSession) return { list: sessionList, realId: null };

  const realUUID = realSession.id;
  (realSession as ExtendedSession).realId = realUUID;
  realSession.id = tempSessionId;
  return {
    list: [realSession, ...sessionList.filter((s) => s !== realSession)],
    realId: realUUID,
  };
};

// ---------------------------------------------------------------------------
// Per-session user message persistence (survives page refresh)
// ---------------------------------------------------------------------------

const STORAGE_PREFIX = "hubos_pending_user_msg_";

// How long a cached pending user message stays valid. Beyond this it is
// considered stale (the user almost certainly closed the page or the run
// finished without our knowing) and must NOT be replayed into the chat
// timeline — otherwise re-entering /chat looks like the page is auto-sending
// a phantom message.
const PENDING_USER_MSG_TTL_MS = 2 * 60 * 1000;

interface PendingUserMsgPayload {
  text: string;
  ts: number;
}

function savePendingUserMessage(sessionId: string, text: string): void {
  try {
    const payload: PendingUserMsgPayload = { text, ts: Date.now() };
    sessionStorage.setItem(
      `${STORAGE_PREFIX}${sessionId}`,
      JSON.stringify(payload),
    );
  } catch {
    /* quota exceeded – ignore */
  }
}

function loadPendingUserMessage(sessionId: string): string {
  try {
    const raw = sessionStorage.getItem(`${STORAGE_PREFIX}${sessionId}`);
    if (!raw) return "";
    // Backward compatibility: older entries were stored as plain strings.
    if (!raw.startsWith("{")) return raw;
    const parsed = JSON.parse(raw) as PendingUserMsgPayload;
    if (!parsed?.text || typeof parsed.ts !== "number") return "";
    if (Date.now() - parsed.ts > PENDING_USER_MSG_TTL_MS) {
      sessionStorage.removeItem(`${STORAGE_PREFIX}${sessionId}`);
      return "";
    }
    return parsed.text;
  } catch {
    return "";
  }
}

function clearPendingUserMessage(sessionId: string): void {
  try {
    sessionStorage.removeItem(`${STORAGE_PREFIX}${sessionId}`);
  } catch {
    /* ignore */
  }
}

/**
 * Drop every cached pending user message regardless of session. Used when the
 * user explicitly returns to `/chat` (no chatId): nothing in flight should
 * be replayed into a freshly opened conversation surface.
 */
export function clearAllPendingUserMessages(): void {
  try {
    const toRemove: string[] = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      if (key && key.startsWith(STORAGE_PREFIX)) toRemove.push(key);
    }
    toRemove.forEach((k) => sessionStorage.removeItem(k));
  } catch {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
// SessionApi
// ---------------------------------------------------------------------------

class SessionApi implements IAgentScopeRuntimeWebUISessionAPI {
  private sessionList: IAgentScopeRuntimeWebUISession[] = [];
  private invalidSessionIds: Set<string> = new Set();

  /**
   * When set, getSessionList will move the matching session to the front on the first call,
   * so the library's useMount auto-selects it instead of always defaulting to sessions[0].
   * Cleared after first use.
   */
  preferredChatId: string | null = null;

  /**
   * Cache the latest user message for a chat so it can be patched into
   * history during reconnect (the backend only persists it after generation
   * completes). Persisted to sessionStorage so it survives page refresh.
   */
  setLastUserMessage(sessionId: string | string[], text: string): void {
    if (!text) return;
    const ids = Array.isArray(sessionId) ? sessionId : [sessionId];
    for (const id of ids) {
      if (id) savePendingUserMessage(id, text);
    }
  }

  /**
   * Deduplicates concurrent getSessionList calls so that two parallel
   * invocations share one network request and write sessionList only once,
   * preserving any realId mappings that were already resolved.
   */
  private sessionListRequest: Promise<IAgentScopeRuntimeWebUISession[]> | null =
    null;

  /**
   * Deduplicates concurrent getSession calls for the same sessionId.
   * Key: sessionId, Value: in-flight promise for getSession.
   */
  private sessionRequests: Map<
    string,
    Promise<IAgentScopeRuntimeWebUISession>
  > = new Map();

  private isKnownInvalidSessionId(sessionId: string): boolean {
    if (!sessionId) return false;
    if (this.invalidSessionIds.has(sessionId)) return true;

    const existing = this.sessionList.find((s) => s.id === sessionId) as
      | ExtendedSession
      | undefined;
    if (!existing) return false;

    return Boolean(
      existing.realId && this.invalidSessionIds.has(existing.realId),
    );
  }

  /**
   * Synchronous "do we already trust this session id?" check, used by
   * ChatPage to avoid tearing the chat surface down during streaming when
   * the URL flips from the local-timestamp placeholder to the backend
   * UUID (or vice versa). Matches against both the visible `id` and the
   * backend `realId` of every session in the in-memory list.
   */
  isKnownSessionId(sessionId: string): boolean {
    if (!sessionId) return false;
    if (this.invalidSessionIds.has(sessionId)) return false;
    return this.sessionList.some((s) => {
      if (s.id === sessionId) return true;
      const realId = (s as ExtendedSession).realId;
      return typeof realId === "string" && realId === sessionId;
    });
  }

  /**
   * Called when a temporary timestamp session id is resolved to a real backend
   * UUID. Consumers (e.g. Chat/index.tsx) can register here to update the URL.
   */
  onSessionIdResolved: ((tempId: string, realId: string) => void) | null = null;

  /**
   * Called after a session is removed. Consumers can register here to clear
   * the session id from the URL.
   */
  onSessionRemoved: ((removedId: string) => void) | null = null;

  /**
   * Called when a session is selected from the session list.
   * Consumers can register here to update the URL when switching sessions.
   */
  onSessionSelected:
    | ((sessionId: string | null | undefined, realId: string | null) => void)
    | null = null;

  /**
   * Called when a new session is created.
   * Consumers can register here to update the URL with the new session id.
   */
  onSessionCreated: ((sessionId: string) => void) | null = null;

  /**
   * When reconnecting to a running conversation, the backend history may not
   * include the latest user message (it's only persisted after generation
   * completes). If generating, look up the cached text from sessionStorage
   * and patch it into the message list.
   *
   * When not generating the conversation is done — clear the cached entry.
   */
  private patchLastUserMessage(
    messages: IAgentScopeRuntimeWebUIMessage[],
    generating: boolean,
    sessionIds: string[],
  ): void {
    if (!generating) {
      for (const id of sessionIds) clearPendingUserMessage(id);
      return;
    }

    // Try all known session IDs to find the cached user message.
    let cachedText = "";
    for (const id of sessionIds) {
      cachedText = loadPendingUserMessage(id);
      if (cachedText) break;
    }
    if (!cachedText) return;

    // Avoid inserting a duplicate user message that already exists in history.
    const hasDuplicate = messages.some((m) => {
      if (m.role !== ROLE_USER) return false;
      const text = extractTextFromContent(
        m?.cards?.[0]?.data?.input?.[0]?.content,
      );
      return text === cachedText;
    });
    if (hasDuplicate) return;

    const userCard = buildUserCard({
      content: [{ type: "text", text: cachedText }],
      role: ROLE_USER,
    } as Message);

    const lastMsg = messages[messages.length - 1];
    if (lastMsg?.role === ROLE_USER) {
      // Existing user card with empty content — fill it.
      const text = extractTextFromContent(
        lastMsg?.cards?.[0]?.data?.input?.[0]?.content,
      );
      if (!text) {
        lastMsg.cards = userCard.cards;
      }
      // else: user card already has matching content — skip.
    } else {
      // Last message is assistant/generating or no messages at all.
      // Insert user card before the last non-user message so the timeline
      // reads: ...userCard → assistantCard.
      if (lastMsg) {
        messages.splice(messages.length - 1, 0, userCard);
      } else {
        messages.push(userCard);
      }
    }
  }

  private createEmptySession(sessionId: string): ExtendedSession {
    window.currentSessionId = sessionId;
    window.currentUserId = DEFAULT_USER_ID;
    window.currentChannel = DEFAULT_CHANNEL;
    return {
      id: sessionId,
      name: DEFAULT_SESSION_NAME,
      sessionId,
      userId: DEFAULT_USER_ID,
      channel: DEFAULT_CHANNEL,
      messages: [],
      meta: {},
    } as ExtendedSession;
  }

  private updateWindowVariables(session: ExtendedSession): void {
    window.currentSessionId = session.sessionId || "";
    window.currentUserId = session.userId || DEFAULT_USER_ID;
    window.currentChannel = session.channel || DEFAULT_CHANNEL;
  }

  private getLocalSession(sessionId: string): IAgentScopeRuntimeWebUISession {
    const local = this.sessionList.find((s) => s.id === sessionId);
    if (local) {
      this.updateWindowVariables(local as ExtendedSession);
      return local;
    }
    return this.createEmptySession(sessionId);
  }

  /**
   * Returns the real backend UUID for a session identified by id (which may be
   * a local timestamp). Returns null when not yet resolved or not found.
   */
  getRealIdForSession(sessionId: string): string | null {
    const s = this.sessionList.find((x) => x.id === sessionId) as
      | ExtendedSession
      | undefined;
    return s?.realId ?? null;
  }

  /** Apply listChats to sessionList; merge realId and generating by session_id. */
  private applyChatsToSessionList(
    chats: ChatSpec[],
  ): IAgentScopeRuntimeWebUISession[] {
    const newList = chats
      .filter((c) => c.id && c.id !== "undefined" && c.id !== "null")
      .filter((c) => !this.invalidSessionIds.has(c.id))
      .map(chatSpecToSession);
    // Keep backend order intact so the runtime selects the newest chat first.
    // XClaw already returns chats in descending recency order.
    this.sessionList = newList.map((s) => {
      const existing = this.sessionList.find(
        (e) =>
          (e as ExtendedSession).sessionId === (s as ExtendedSession).sessionId,
      ) as ExtendedSession | undefined;
      if (!existing) return s;
      const next = { ...s } as ExtendedSession;
      if (existing.realId) {
        next.id = existing.id;
        next.realId = existing.realId;
      }
      if (existing.generating !== undefined) {
        next.generating = existing.generating;
      }
      return next as IAgentScopeRuntimeWebUISession;
    });

    return [...this.sessionList];
  }

  async getSessionList() {
    if (this.sessionListRequest) return this.sessionListRequest;

    this.sessionListRequest = (async () => {
      try {
        const chats = await api.listChats();
        return this.applyChatsToSessionList(chats);
      } finally {
        this.sessionListRequest = null;
      }
    })();

    return this.sessionListRequest;
  }

  /** Track the last session ID that triggered onSessionSelected to avoid duplicate calls. */
  private lastSelectedSessionId: string | null = null;

  async getSession(sessionId: string) {
    if (this.isKnownInvalidSessionId(sessionId)) {
      this.removeSessionLocally(sessionId);
      return this.createEmptySession(Date.now().toString());
    }

    const existingRequest = this.sessionRequests.get(sessionId);
    if (existingRequest) return existingRequest;

    const requestPromise = this._doGetSession(sessionId);
    this.sessionRequests.set(sessionId, requestPromise);

    try {
      const session = await requestPromise;
      // Trigger onSessionSelected only when session actually changes
      if (sessionId !== this.lastSelectedSessionId) {
        this.lastSelectedSessionId = sessionId;
        const extendedSession = session as ExtendedSession;
        const realId = extendedSession.realId || null;
        this.onSessionSelected?.(sessionId, realId);
      }
      return session;
    } finally {
      this.sessionRequests.delete(sessionId);
    }
  }

  private async _doGetSession(
    sessionId: string,
  ): Promise<IAgentScopeRuntimeWebUISession> {
    // --- Local timestamp ID (New Chat before first reply) ---
    if (isLocalTimestamp(sessionId)) {
      const fromList = this.sessionList.find((s) => s.id === sessionId) as
        | ExtendedSession
        | undefined;

      // If realId is already resolved, use it directly to fetch history.
      if (fromList?.realId) {
        try {
          const chatHistory = await api.getChat(fromList.realId);
          const generating = isGenerating(chatHistory);
          const messages = convertMessages(chatHistory.messages || []);
          this.patchLastUserMessage(messages, generating, [
            sessionId,
            fromList.realId,
          ]);
          const session: ExtendedSession = {
            id: sessionId,
            name: fromList.name || DEFAULT_SESSION_NAME,
            sessionId: fromList.sessionId || sessionId,
            userId: fromList.userId || DEFAULT_USER_ID,
            channel: fromList.channel || DEFAULT_CHANNEL,
            messages,
            meta: fromList.meta || {},
            realId: fromList.realId,
            generating,
          };
          this.updateWindowVariables(session);
          return session;
        } catch (err) {
          if (isThreadNotFoundError(err)) {
            await this.purgeInvalidBackendSession(fromList.realId);
            this.removeSessionLocally(sessionId);
            return this.createEmptySession(Date.now().toString());
          }
          throw err;
        }
      }

      // Pure local session (not yet sent to backend): wait until updateSession
      // resolves the realId, then fetch history with the real UUID.
      await new Promise<void>((resolve, reject) => {
        let attempts = 0;
        const maxAttempts = 50; // 5 seconds max (50 × 100ms)
        const check = () => {
          const s = this.sessionList.find((x) => x.id === sessionId) as
            | ExtendedSession
            | undefined;
          if (s?.realId) {
            resolve();
          } else if (++attempts >= maxAttempts) {
            reject(
              new Error(`waitForRealId timed out for session ${sessionId}`),
            );
          } else {
            setTimeout(check, 100);
          }
        };
        setTimeout(check, 100);
      });

      const refreshed = this.sessionList.find((s) => s.id === sessionId) as
        | ExtendedSession
        | undefined;
      if (refreshed?.realId) {
        try {
          const chatHistory = await api.getChat(refreshed.realId);
          const generating = isGenerating(chatHistory);
          const messages = convertMessages(chatHistory.messages || []);
          this.patchLastUserMessage(messages, generating, [
            sessionId,
            refreshed.realId,
          ]);
          const session: ExtendedSession = {
            id: sessionId,
            name: refreshed.name || DEFAULT_SESSION_NAME,
            sessionId: refreshed.sessionId || sessionId,
            userId: refreshed.userId || DEFAULT_USER_ID,
            channel: refreshed.channel || DEFAULT_CHANNEL,
            messages,
            meta: refreshed.meta || {},
            realId: refreshed.realId,
            generating,
          };
          this.updateWindowVariables(session);
          return session;
        } catch (err) {
          if (isThreadNotFoundError(err)) {
            await this.purgeInvalidBackendSession(refreshed.realId);
            this.removeSessionLocally(sessionId);
            return this.createEmptySession(Date.now().toString());
          }
          throw err;
        }
      }

      return this.getLocalSession(sessionId);
    }

    // --- No session selected (e.g. after delete) ---
    if (!sessionId || sessionId === "undefined" || sessionId === "null") {
      return this.createEmptySession(Date.now().toString());
    }

    // --- Regular backend UUID ---
    // Guard: verify the session exists in our local list before hitting the
    // backend. Unknown IDs (stale URLs, race conditions) should not generate
    // 404s — return an empty session instead and let the caller recover.
    //
    // Match by BOTH s.id and s.realId: when a session's id is still the local
    // timestamp placeholder, its backend UUID lives in realId. If the URL has
    // already been promoted to the real UUID, we must find it via realId too.
    const matchSession = (s: IAgentScopeRuntimeWebUISession) =>
      s.id === sessionId || (s as ExtendedSession).realId === sessionId;

    let fromList = this.sessionList.find(matchSession) as
      | ExtendedSession
      | undefined;

    if (!fromList) {
      // Not in current cache — do one refresh to pick up recently-created chats.
      await this.getSessionList();
      fromList = this.sessionList.find(matchSession) as
        | ExtendedSession
        | undefined;
      if (!fromList) {
        // Still not found after refresh — session is truly unknown.
        // Return an empty session; do NOT call the backend to avoid a 404.
        return this.createEmptySession(Date.now().toString());
      }
    }

    try {
      const chatHistory = await api.getChat(sessionId);
      const generating = isGenerating(chatHistory);
      const messages = convertMessages(chatHistory.messages || []);
      this.patchLastUserMessage(
        messages,
        generating,
        [
          sessionId,
          fromList?.id ?? "",
          (fromList as ExtendedSession)?.realId ?? "",
        ].filter(Boolean),
      );
      const session: ExtendedSession = {
        id: sessionId,
        name: fromList?.name || sessionId,
        sessionId: fromList?.sessionId || sessionId,
        userId: fromList?.userId || DEFAULT_USER_ID,
        channel: fromList?.channel || DEFAULT_CHANNEL,
        messages,
        meta: fromList?.meta || {},
        generating,
      };

      this.updateWindowVariables(session);
      return session;
    } catch (err) {
      if (isThreadNotFoundError(err)) {
        await this.purgeInvalidBackendSession(sessionId);
        this.removeSessionLocally(sessionId);
        return this.createEmptySession(Date.now().toString());
      }
      throw err;
    }
  }

  async updateSession(session: Partial<IAgentScopeRuntimeWebUISession>) {
    session.messages = [];
    const index = this.sessionList.findIndex((s) => s.id === session.id);

    if (index > -1) {
      this.sessionList[index] = { ...this.sessionList[index], ...session };

      const existing = this.sessionList[index] as ExtendedSession;
      if (isLocalTimestamp(existing.id) && !existing.realId) {
        const tempId = existing.id;
        this.getSessionList().then(() => {
          const { list, realId } = resolveRealId(this.sessionList, tempId);
          this.sessionList = list;
          if (realId) {
            this.onSessionIdResolved?.(tempId, realId);
          }
        });
      }
    } else {
      const tempId = session.id!;
      await this.getSessionList().then(() => {
        const { list, realId } = resolveRealId(this.sessionList, tempId);
        this.sessionList = list;
        if (realId) {
          this.onSessionIdResolved?.(tempId, realId);
        }
      });
    }

    return [...this.sessionList];
  }

  async createSession(session: Partial<IAgentScopeRuntimeWebUISession>) {
    session.id = Date.now().toString();

    const extended: ExtendedSession = {
      ...session,
      sessionId: session.id,
      userId: DEFAULT_USER_ID,
      channel: DEFAULT_CHANNEL,
    } as ExtendedSession;

    this.updateWindowVariables(extended);
    // this.sessionList.unshift(extended);
    this.onSessionCreated?.(session.id);
    return this.sessionList;
  }

  /**
   * Remove a session from the local list without calling the backend API.
   * Used when a session is found to be stale/invalid (e.g., 404 "Thread not found").
   */
  private removeSessionLocally(sessionId: string): void {
    const existing = this.sessionList.find((s) => s.id === sessionId) as
      | ExtendedSession
      | undefined;
    const resolvedId = existing?.realId ?? sessionId;
    this.invalidSessionIds.add(resolvedId);
    this.invalidSessionIds.add(sessionId);
    this.sessionList = this.sessionList.filter((s) => s.id !== sessionId);
    if (
      this.preferredChatId === sessionId ||
      this.preferredChatId === resolvedId
    ) {
      this.preferredChatId = null;
    }
    if (
      this.lastSelectedSessionId === sessionId ||
      this.lastSelectedSessionId === resolvedId
    ) {
      this.lastSelectedSessionId = null;
    }
    this.onSessionRemoved?.(resolvedId);
  }

  private async purgeInvalidBackendSession(sessionId: string): Promise<void> {
    this.invalidSessionIds.add(sessionId);
    try {
      await api.deleteChat(sessionId);
    } catch {
      // Ignore purge errors; local blacklist still prevents immediate re-selection.
    }
  }

  async removeSession(session: Partial<IAgentScopeRuntimeWebUISession>) {
    if (!session.id) return [...this.sessionList];

    const { id: sessionId } = session;

    const existing = this.sessionList.find((s) => s.id === sessionId) as
      | ExtendedSession
      | undefined;

    const deleteId =
      existing?.realId ?? (isLocalTimestamp(sessionId) ? null : sessionId);

    // If the backend returns 404 (already deleted), still clean up locally.
    if (deleteId) {
      try {
        await api.deleteChat(deleteId);
      } catch (err) {
        if (!isThreadNotFoundError(err)) throw err;
        // Session already gone — continue with local cleanup
      }
    }

    this.sessionList = this.sessionList.filter((s) => s.id !== sessionId);

    const resolvedId = existing?.realId ?? sessionId;
    this.onSessionRemoved?.(resolvedId);

    return [...this.sessionList];
  }
}

export default new SessionApi();
