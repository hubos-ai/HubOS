// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
import { chatApi } from "../../api/modules/chat";
export type CopyableContent = {
  type?: string;
  text?: string;
  refusal?: string;
};

export type CopyableMessage = {
  role?: string;
  content?: string | CopyableContent[];
};

export type CopyableResponse = {
  output?: CopyableMessage[];
};

export type RuntimeLoadingBridgeApi = {
  getLoading?: () => boolean | string;
  setLoading?: (loading: boolean | string) => void;
};

type RuntimeResponseChunk =
  | {
      object: "response";
      status?: string;
      output?: Array<Record<string, unknown>>;
    }
  | {
      object: "message";
      id: string;
      role: "assistant";
      type: string;
      status: string;
      content: Array<Record<string, unknown>>;
      message?: string;
      code?: string;
    };

const STATUS_TOOL_NAMES = new Set([
  "Context understanding",
  "Experience matching",
  "Knowledge injection",
]);

function normalizeToolData(
  rawData: Record<string, unknown>,
  fallbackId: string,
): Record<string, unknown> {
  return {
    ...rawData,
    call_id: rawData.call_id || rawData.tool_use_id || rawData.id || fallbackId,
  };
}

function getToolKey(data: Record<string, unknown>, fallbackId: string): string {
  return String(
    data.call_id || data.tool_use_id || data.id || data.name || fallbackId,
  );
}

function isStatusToolData(data: Record<string, unknown>): boolean {
  return typeof data.name === "string" && STATUS_TOOL_NAMES.has(data.name);
}

function isStatusToolKey(inputContent?: Record<string, unknown>): boolean {
  const inputData =
    inputContent?.data && typeof inputContent.data === "object"
      ? (inputContent.data as Record<string, unknown>)
      : undefined;
  return Boolean(inputData && isStatusToolData(inputData));
}

function hasToolOutput(data: Record<string, unknown>): boolean {
  return (
    data.output !== undefined ||
    data.result !== undefined ||
    data.text !== undefined ||
    data.content !== undefined
  );
}

function hasToolInput(data: Record<string, unknown>): boolean {
  return data.arguments !== undefined || data.input !== undefined;
}

// ---------------------------------------------------------------------------
// Text extraction utilities
// ---------------------------------------------------------------------------

/** Extract copyable text from assistant response. */
export function extractCopyableText(response: CopyableResponse): string {
  const collectText = (assistantOnly: boolean) => {
    const chunks = (response.output || []).flatMap((item: CopyableMessage) => {
      if (assistantOnly && item.role !== "assistant") return [];

      if (typeof item.content === "string") {
        return [item.content];
      }

      if (!Array.isArray(item.content)) {
        return [];
      }

      return item.content.flatMap((content: CopyableContent) => {
        if (content.type === "text" && typeof content.text === "string") {
          return [content.text];
        }

        if (content.type === "refusal" && typeof content.refusal === "string") {
          return [content.refusal];
        }

        return [];
      });
    });

    return chunks.filter(Boolean).join("\n\n").trim();
  };

  return collectText(true) || JSON.stringify(response);
}

/** Extract plain text from user message content. */
export function extractUserMessageText(m: any): string {
  if (typeof m.content === "string") return m.content;
  if (!Array.isArray(m.content)) return "";
  return m.content
    .filter((p: any) => p.type === "text")
    .map((p: any) => p.text || "")
    .join("\n");
}

// ---------------------------------------------------------------------------
// Clipboard utilities
// ---------------------------------------------------------------------------

/** Copy text to clipboard with fallback for non-secure contexts. */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);

  let copied = false;
  try {
    textarea.focus();
    textarea.select();
    copied = document.execCommand("copy");
  } finally {
    document.body.removeChild(textarea);
  }

  if (!copied) {
    throw new Error("Failed to copy text");
  }
}

// ---------------------------------------------------------------------------
// Error response utilities
// ---------------------------------------------------------------------------

/** Build a 400 error response when model is not configured. */
export function buildModelError(): Response {
  return new Response(
    JSON.stringify({
      error: "Model not configured",
      message: "Please configure a model first",
    }),
    { status: 400, headers: { "Content-Type": "application/json" } },
  );
}

// ---------------------------------------------------------------------------
// SSE compatibility utilities
// ---------------------------------------------------------------------------
//
// The XClaw / HubOS gateway streams LangGraph events directly:
//
//   event: metadata        data: {"thread_id":..., "status":"started"}
//   event: messages-tuple  data: {"type":"ai", "content":"<token>", "id":...}
//   event: values          data: {"output":[{role:"ai|human|tool", content,...}]}
//   event: error           data: {"error":"..."}
//   event: end             data: null
//
// The @agentscope-ai/chat library, however, only understands AgentScope
// Runtime envelopes (`object: "message" | "response" | "content"`). If we
// hand the raw LangGraph chunks straight to its Builder, every chunk is
// dropped silently and the chat keeps spinning forever.
//
// Below we translate the LangGraph stream into Builder-friendly objects.
// The translator is stateful: it accumulates per-message-id token text so
// streaming AI replies render incrementally, and it emits a terminal
// `object: "response"` chunk with `status: "completed"` so the chat knows
// the run finished and the loading spinner can be cleared.

interface AgentScopeStreamMessage {
  object: "message";
  id: string;
  role: "assistant" | "tool" | "user" | "system";
  type: string;
  status: string;
  content: Array<Record<string, unknown>>;
  message?: string;
  code?: string;
}

interface AgentScopeStreamResponse {
  object: "response";
  status?: string;
  output?: Array<Record<string, unknown>>;
}

type RuntimeStreamChunk =
  | AgentScopeStreamMessage
  | AgentScopeStreamResponse
  | RuntimeResponseChunk;

/** A stateful SSE chunk parser with an explicit reset method. */
export interface SseChunkParser {
  (raw: string): RuntimeStreamChunk;
  /** Clear all accumulated stream state (output messages, accumulators). */
  reset: () => void;
}

/** Map LangChain BaseMessage discriminators to AgentScope message types. */
function mapLangchainTypeToAgentscope(rawType: string): {
  type: string;
  role: AgentScopeStreamMessage["role"];
} {
  switch (rawType) {
    case "ai":
    case "AIMessage":
    case "AIMessageChunk":
    case "assistant":
      return { type: "message", role: "assistant" };
    case "reasoning":
      return { type: "reasoning", role: "assistant" };
    case "plugin_call":
    case "function_call":
    case "component_call":
    case "mcp_call":
      return { type: rawType, role: "assistant" };
    case "plugin_call_output":
    case "function_call_output":
    case "component_call_output":
    case "mcp_call_output":
      return { type: rawType, role: "tool" };
    case "tool":
    case "ToolMessage":
      return { type: "plugin_call_output", role: "tool" };
    case "human":
    case "HumanMessage":
    case "user":
      return { type: "message", role: "user" };
    case "system":
    case "SystemMessage":
      return { type: "message", role: "system" };
    default:
      return { type: "message", role: "assistant" };
  }
}

/** Coerce LangGraph content (string | array | unknown) into AgentScope IContent[]. */
function toAgentScopeContent(
  content: unknown,
  status: string,
): Array<Record<string, unknown>> {
  if (typeof content === "string") {
    return [{ type: "text", text: content, status }];
  }
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (part && typeof part === "object") {
        const p = part as Record<string, unknown>;
        if (typeof p.text === "string" && (!p.type || p.type === "text")) {
          return { type: "text", text: p.text, status };
        }
        return { status, ...p };
      }
      return { type: "text", text: String(part ?? ""), status };
    });
  }
  if (content == null || content === "") return [];
  return [{ type: "text", text: String(content), status }];
}

/**
 * Build a stateful SSE chunk parser bound to the lifetime of a chat surface.
 *
 * Returns a function suitable for use as `apiOptions.responseParser` in
 * `@agentscope-ai/chat`. The closure tracks accumulated assistant text
 * per message id and the running list of output messages so that:
 *
 * - Streamed AI tokens render incrementally without losing earlier text
 * - Tool messages join the same response card
 * - The terminal `null` chunk emits a Builder-friendly Completed envelope
 *   carrying the full output list (otherwise Builder.handleResponse would
 *   wipe the accumulated messages with `output: []`)
 * - The very first `metadata: started` event resets state, so back-to-back
 *   conversations never bleed into each other.
 */
export function createSseChunkParser(
  onPlanCreated?: (data: Record<string, unknown>) => void,
): SseChunkParser {
  const accumulators = new Map<string, string>();
  const contentAccumulators = new Map<string, string>();
  const toolInputContent = new Map<string, Record<string, unknown>>();
  const messageMeta = new Map<
    string,
    Pick<AgentScopeStreamMessage, "id" | "role" | "type">
  >();
  let outputMessages: AgentScopeStreamMessage[] = [];
  let assistantSeq = 0;
  let activeStreamId: string | null = null;

  const reset = () => {
    accumulators.clear();
    contentAccumulators.clear();
    toolInputContent.clear();
    messageMeta.clear();
    outputMessages = [];
    assistantSeq = 0;
    activeStreamId = null;
  };

  const upsertMessage = (msg: AgentScopeStreamMessage) => {
    const idx = outputMessages.findIndex((m) => m.id === msg.id);
    if (idx >= 0) outputMessages[idx] = msg;
    else outputMessages.push(msg);
  };

  const keepAlive = (): AgentScopeStreamResponse => ({
    object: "response",
    status: "in_progress",
    output: outputMessages.map((m) => ({ ...m })),
  });

  const buildStatusToolMessage = (
    key: string,
    outputData: Record<string, unknown>,
    status?: string,
  ): AgentScopeStreamMessage => {
    const inputContent = toolInputContent.get(key) || {
      type: "data",
      data: {
        call_id: key,
        name: typeof outputData.name === "string" ? outputData.name : "Status",
        arguments: "{}",
      },
      status: "completed",
    };

    const inputData =
      inputContent.data && typeof inputContent.data === "object"
        ? (inputContent.data as Record<string, unknown>)
        : {};
    const mergedOutputData = {
      ...outputData,
      call_id: outputData.call_id || inputData.call_id || key,
      name: outputData.name || inputData.name || "Status",
    };

    return {
      object: "message",
      id: `status-tool-${key}`,
      role: "assistant",
      type: "plugin_call",
      status: status || "completed",
      content: [
        inputContent,
        {
          type: "data",
          data: mergedOutputData,
          status: status || "completed",
        },
      ],
    };
  };

  const buildStatusToolStartMessage = (
    key: string,
    inputContent: Record<string, unknown>,
    status?: string,
  ): AgentScopeStreamMessage => ({
    object: "message",
    id: `status-tool-${key}`,
    role: "assistant",
    type: "plugin_call",
    status: status || "in_progress",
    content: [inputContent],
  });

  const finalize = (
    status: "completed" | "failed",
    extra?: { error?: string },
  ): AgentScopeStreamResponse => {
    const result: AgentScopeStreamResponse = {
      object: "response",
      status,
      output: outputMessages.map((m) => ({ ...m, status: "completed" })),
    };
    if (extra?.error) {
      // Surface error inline so Card renders it even if no content arrived.
      result.output!.push({
        object: "message",
        id: `runtime-error-${Date.now()}`,
        role: "assistant",
        type: "error",
        status: "failed",
        content: [],
        code: "backend_stream_error",
        message: extra.error,
      });
    }
    reset();
    return result;
  };

  const parseChunk = function (raw: string): RuntimeStreamChunk {
    // Guard: the library may pass null/undefined at end-of-stream.
    // Using optional chaining instead of raw.trim() directly avoids a
    // TypeError that would leave the loading spinner stuck forever.
    if (raw == null) return finalize("completed");

    const trimmed = raw.trim();

    // Terminal marker — backend emits `event: end\ndata: null`.
    if (!trimmed || trimmed === "null") {
      return finalize("completed");
    }

    let data: any;
    try {
      data = JSON.parse(trimmed);
    } catch {
      return {
        object: "message",
        id: "runtime-parse-error",
        role: "assistant",
        type: "error",
        status: "failed",
        content: [],
        code: "invalid_sse_payload",
        message: trimmed,
      };
    }

    // Pass-through for chunks already shaped as AgentScope envelopes.
    if (data && typeof data.object === "string") {
      // A non-terminal response without output is just stream/run metadata.
      // Passing it through as `output: []` makes AgentScope's Builder replace
      // the accumulated output with an empty array, which can erase a real
      // reasoning/thinking message that arrived just before a heartbeat or
      // response-start event. Preserve the current output instead.
      if (
        data.object === "response" &&
        !Array.isArray(data.output) &&
        data.status !== "completed" &&
        data.status !== "failed"
      ) {
        return keepAlive();
      }

      // AgentScope's useChatRequest only calls updateMessage when
      // Builder.data has content in output[0]. Content-delta events
      // (object: "content") are handled by Builder's handleContent which
      // correctly accumulates deltas, but the resulting Builder.data still
      // has content → useChatRequest skips the updateMessage call.
      //
      // To force a re-render after each content delta, we convert the
      // content event into a message event.  We track accumulated text per
      // msg_id so the fake message carries the full text so far (not just
      // the delta), which keeps Builder's output consistent.
      if (data.object === "content" && typeof data.msg_id === "string") {
        // DataContent (type: "data") carries structured tool data (name,
        // arguments, output) — NOT text deltas.  Builder.handleContent only
        // works if a matching message shell is already present. We deliberately
        // do not keep empty shells in outputMessages because they can block
        // streaming thinking updates, so synthesize a full message here instead
        // of passing the content event through and letting Builder drop it.
        if (data.type === "data") {
          const rawData =
            data.data && typeof data.data === "object"
              ? (data.data as Record<string, unknown>)
              : {};
          const normalizedData = normalizeToolData(rawData, data.msg_id);
          const toolKey = getToolKey(normalizedData, data.msg_id);

          if (hasToolInput(normalizedData) && !hasToolOutput(normalizedData)) {
            toolInputContent.set(toolKey, {
              type: "data",
              data: normalizedData,
              status: data.status || "in_progress",
            });
          }

          // Status tools are tiny pre-agent diagnostics. Feed AgentScope a
          // fully-merged assistant tool message as soon as output arrives
          // instead of relying on plugin_call/plugin_call_output pairing,
          // because the stream sends these as separate message ids.
          const cachedInput = toolInputContent.get(toolKey);
          const isStatusTool =
            isStatusToolData(normalizedData) || isStatusToolKey(cachedInput);

          if (isStatusTool) {
            if (!hasToolOutput(normalizedData)) {
              // Render the stage label immediately, then replace this same
              // card with the completed output when tool_result arrives.
              const inputContent = cachedInput || {
                type: "data",
                data: normalizedData,
                status: data.status || "in_progress",
              };
              const converted = buildStatusToolStartMessage(
                toolKey,
                inputContent,
                data.status || "in_progress",
              );
              upsertMessage(converted);
              return keepAlive();
            }
            const converted = buildStatusToolMessage(
              toolKey,
              normalizedData,
              data.status || "completed",
            );
            upsertMessage(converted);
            return keepAlive();
          }

          const msg =
            outputMessages.find((m) => m.id === data.msg_id) ||
            messageMeta.get(data.msg_id);
          const converted: AgentScopeStreamMessage = {
            object: "message",
            id: data.msg_id,
            role: msg?.role || "assistant",
            type: msg?.type || "message",
            status: data.status || "in_progress",
            content: [
              {
                type: "data",
                data: normalizedData,
                status: data.status || "in_progress",
              },
            ],
          };
          upsertMessage(converted);
          return keepAlive();
        }

        const text = typeof data.text === "string" ? data.text : "";
        const prev = contentAccumulators.get(data.msg_id) || "";
        const full = data.delta ? prev + text : text;
        contentAccumulators.set(data.msg_id, full);

        // Look up the message type from metadata.  We intentionally do not
        // upsert empty message shells into outputMessages because the
        // AgentScope chat hook only decides whether to update by checking
        // output[0].content.length.  A leading empty shell would make real
        // reasoning/content deltas later in the output invisible.
        const msg =
          outputMessages.find((m) => m.id === data.msg_id) ||
          messageMeta.get(data.msg_id);
        const converted: AgentScopeStreamMessage = {
          object: "message",
          id: data.msg_id,
          role: msg?.role || "assistant",
          type: msg?.type || "message",
          status: "in_progress",
          content: full
            ? [{ type: "text", text: full, status: "in_progress" }]
            : [],
        };
        upsertMessage(converted);
        return keepAlive();
      }

      // Track message metadata for content→message conversion above.
      if (data.object === "message") {
        const msg = data as AgentScopeStreamMessage;
        messageMeta.set(msg.id, {
          id: msg.id,
          role: msg.role,
          type: msg.type,
        });

        const firstContent = Array.isArray(msg.content)
          ? (msg.content[0] as { data?: Record<string, unknown> } | undefined)
          : undefined;
        const rawToolData =
          firstContent?.data && typeof firstContent.data === "object"
            ? firstContent.data
            : undefined;
        if (rawToolData) {
          const normalizedData = normalizeToolData(rawToolData, msg.id);
          const toolKey = getToolKey(normalizedData, msg.id);
          const cachedInput = toolInputContent.get(toolKey);
          const isStatusTool =
            isStatusToolData(normalizedData) || isStatusToolKey(cachedInput);

          if (isStatusTool) {
            if (!hasToolOutput(normalizedData)) {
              const inputContent = {
                type: "data",
                data: normalizedData,
                status: msg.status || "completed",
              };
              toolInputContent.set(toolKey, inputContent);
              const converted = buildStatusToolStartMessage(
                toolKey,
                inputContent,
                msg.status || "in_progress",
              );
              upsertMessage(converted);
              return keepAlive();
            }
            const converted = buildStatusToolMessage(
              toolKey,
              normalizedData,
              msg.status || "completed",
            );
            upsertMessage(converted);
            return keepAlive();
          }
        }

        // Do not push empty message shells into output. They can block
        // AgentScope's update guard and hide later reasoning/content.
        if (Array.isArray(msg.content) && msg.content.length > 0) {
          upsertMessage(msg);
          return keepAlive();
        }
      }

      return data as RuntimeResponseChunk;
    }

    // Stream start: backend may emit {thread_id, status:"started"} first.
    // Also check for _hubos_stream_id which TaskTracker injects on every new
    // run.  When the stream id changes, all accumulated state is cleared so
    // the old stream's output cannot bleed into the new one.
    if (data && (data.thread_id || data.status === "started")) {
      const newSid =
        typeof data._hubos_stream_id === "string"
          ? data._hubos_stream_id
          : null;
      if (newSid && newSid !== activeStreamId) {
        reset();
        activeStreamId = newSid;
      } else if (outputMessages.length === 0) {
        reset();
      }
      return keepAlive();
    }

    // Hard error from backend.
    if (data && typeof data.error === "string") {
      return finalize("failed", { error: data.error });
    }

    // Final `event: values` snapshot — full thread message list.
    if (data && Array.isArray(data.output)) {
      // Replace accumulator with the authoritative final state, filtering
      // out the user echo so the assistant card doesn't render the prompt.
      const next: AgentScopeStreamMessage[] = [];
      for (const raw of data.output) {
        if (!raw || typeof raw !== "object") continue;
        const r = raw as Record<string, unknown>;
        // Final values snapshots can carry `role: "assistant"` together with
        // a more specific `type` such as `reasoning`. Prefer `type` first so
        // Thinking blocks survive history/final-snapshot reconstruction.
        const rawType =
          typeof r.type === "string"
            ? (r.type as string)
            : typeof r.role === "string"
            ? (r.role as string)
            : "ai";
        const mapped = mapLangchainTypeToAgentscope(rawType);
        if (mapped.role === "user" || mapped.role === "system") continue;
        const id =
          typeof r.id === "string" && r.id
            ? (r.id as string)
            : `final-${next.length}`;
        const content = toAgentScopeContent(r.content, "completed");
        const statusContent = content.find((item) => {
          const dataItem = item.data;
          if (!dataItem || typeof dataItem !== "object") return false;
          const record = dataItem as Record<string, unknown>;
          return (
            record.kind === "hubos_status" ||
            record.type === "hubos_status" ||
            isStatusToolData(record)
          );
        });
        if (statusContent?.data && typeof statusContent.data === "object") {
          const normalizedData = normalizeToolData(
            statusContent.data as Record<string, unknown>,
            id,
          );
          const key = getToolKey(normalizedData, id);
          next.push(
            hasToolOutput(normalizedData)
              ? buildStatusToolMessage(key, normalizedData, "completed")
              : buildStatusToolStartMessage(
                  key,
                  {
                    type: "data",
                    data: normalizedData,
                    status: "completed",
                  },
                  "completed",
                ),
          );
          continue;
        }
        next.push({
          object: "message",
          id,
          role: mapped.role,
          type: mapped.type,
          status: "completed",
          content,
        });
      }
      outputMessages = next;
      // Don't finalize yet — the `event: end` chunk will flush.
      return {
        object: "response",
        status: "in_progress",
        output: outputMessages.map((m) => ({ ...m })),
      };
    }

    // Title-only values event — irrelevant for the message body.
    if (data && typeof data.title === "string" && !data.content) {
      return keepAlive();
    }

    if (data?.plan_created && typeof onPlanCreated === "function") {
      onPlanCreated(data as Record<string, unknown>);
    }

    // Streaming token chunk: `event: messages-tuple` with {type, content, id}.
    if (data && typeof data.type === "string") {
      const mapped = mapLangchainTypeToAgentscope(data.type as string);
      // Skip user echoes – they're already rendered by the request card.
      if (mapped.role === "user" || mapped.role === "system") {
        return keepAlive();
      }

      const rawId =
        (typeof data.id === "string" && data.id) ||
        (mapped.role === "assistant"
          ? `assistant-${assistantSeq || (assistantSeq = 1)}`
          : `tool-${outputMessages.length}`);

      const incoming =
        typeof data.content === "string"
          ? data.content
          : Array.isArray(data.content)
          ? data.content
              .map((p: any) => (typeof p?.text === "string" ? p.text : ""))
              .join("")
          : "";

      // Tool messages arrive whole, not as deltas – overwrite, don't append.
      const accumulated =
        mapped.role === "tool"
          ? incoming
          : (accumulators.get(rawId) || "") + incoming;
      accumulators.set(rawId, accumulated);

      if (!accumulated) {
        return keepAlive();
      }

      const msg: AgentScopeStreamMessage = {
        object: "message",
        id: rawId,
        role: mapped.role,
        type: mapped.type,
        status: "in_progress",
        content: [{ type: "text", text: accumulated, status: "in_progress" }],
      };
      upsertMessage(msg);
      return keepAlive();
    }

    // Anything else (pure metadata, heartbeats) — keep the spinner alive.
    return keepAlive();
  };

  parseChunk.reset = reset;
  return parseChunk;
}

/**
 * Backwards-compatible single-shot parser. Each call gets a fresh closure,
 * so it cannot accumulate stream state — kept only so external callers
 * importing the old name continue to compile.
 *
 * Prefer `createSseChunkParser` and reuse the returned function across
 * the lifetime of a single chat surface.
 */
export function parseRuntimeSseChunk(raw: string): RuntimeStreamChunk {
  return createSseChunkParser()(raw);
}

// ---------------------------------------------------------------------------
// URL normalization utilities
// ---------------------------------------------------------------------------

/** Decode each path segment; keeps `/` delimiters (including repeated `/`). */
function decodeUriPathSegments(path: string): string {
  return path
    .split("/")
    .map((segment) => {
      if (!segment) return segment;
      try {
        return decodeURIComponent(segment);
      } catch {
        return segment;
      }
    })
    .join("/");
}

/** Convert file URL to stored path for backend: keep full path after `/files/preview/`. */
export function toStoredName(v: string): string {
  const marker = "/files/preview/";
  const idx = v.indexOf(marker);
  if (idx !== -1) {
    let rest = v.slice(idx + marker.length);
    const q = rest.indexOf("?");
    if (q !== -1) rest = rest.slice(0, q);
    const h = rest.indexOf("#");
    if (h !== -1) rest = rest.slice(0, h);
    if (rest) {
      const decoded = decodeUriPathSegments(rest);
      // Windows absolute path: C:\... or C:/...
      const isWindowsAbsolute = /^[a-zA-Z]:[\\/]/.test(decoded);
      if (isWindowsAbsolute) return decoded;
      return decoded.startsWith("/") ? decoded : `/${decoded}`;
    }
  }
  return v;
}

/** Convert content part URLs to stored name format. */
export function normalizeContentUrls(part: any): any {
  const p = { ...part };
  if (p.type === "image" && typeof p.image_url === "string")
    p.image_url = toStoredName(p.image_url);
  if (p.type === "file" && typeof p.file_url === "string")
    p.file_url = toStoredName(p.file_url);
  if (p.type === "audio" && typeof p.data === "string")
    p.data = toStoredName(p.data);
  if (p.type === "video" && typeof p.video_url === "string")
    p.video_url = toStoredName(p.video_url);
  return p;
}

/** Turn a backend content URL (path or full URL) into a full URL for display. */
export function toDisplayUrl(url: string | undefined): string {
  if (!url) return "";
  if (url.startsWith("http://") || url.startsWith("https://")) return url;
  if (url.startsWith("file://")) url = url.replace("file://", "");
  return chatApi.filePreviewUrl(url.startsWith("/") ? url : `/${url}`);
}

// ---------------------------------------------------------------------------
// Runtime notice injection
// ---------------------------------------------------------------------------

/**
 * Inject a runtime notice message into the chat timeline.
 *
 * Uses `chatRef.current.messages.updateMessage()` which appends when
 * the message id does not already exist in the list.
 */
export function appendRuntimeNotice(
  chatRef: {
    current: {
      messages: {
        updateMessage: (msg: Record<string, unknown> & { id: string }) => void;
      };
    } | null;
  },
  text: string,
): void {
  const messagesApi = chatRef.current?.messages;
  if (!messagesApi) return;

  const now = Date.now();
  messagesApi.updateMessage({
    id: `notice-${now}`,
    role: "assistant",
    cards: [
      {
        code: "AgentScopeRuntimeResponseCard",
        data: {
          id: `response-${now}`,
          output: [
            {
              id: `msg-${now}`,
              type: "message",
              role: "assistant",
              content: [{ type: "text", text, status: "completed" }],
              metadata: null,
            },
          ],
          object: "response",
          status: "completed",
          created_at: Math.floor(now / 1000),
          completed_at: Math.floor(now / 1000),
          error: null,
          usage: null,
        },
      },
    ],
    msgStatus: "finished",
  });
}
