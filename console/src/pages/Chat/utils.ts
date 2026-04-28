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
export function createSseChunkParser(): (raw: string) => RuntimeStreamChunk {
  const accumulators = new Map<string, string>();
  let outputMessages: AgentScopeStreamMessage[] = [];
  let assistantSeq = 0;

  const reset = () => {
    accumulators.clear();
    outputMessages = [];
    assistantSeq = 0;
  };

  const upsertMessage = (msg: AgentScopeStreamMessage) => {
    const idx = outputMessages.findIndex((m) => m.id === msg.id);
    if (idx >= 0) outputMessages[idx] = msg;
    else outputMessages.push(msg);
  };

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

  return function parseChunk(raw: string): RuntimeStreamChunk {
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
      return data as RuntimeResponseChunk;
    }

    // Stream start: backend emits {thread_id, status:"started"} first.
    if (data && (data.thread_id || data.status === "started")) {
      reset();
      return { object: "response", status: "in_progress", output: [] };
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
        const rawType =
          typeof r.role === "string"
            ? (r.role as string)
            : typeof r.type === "string"
            ? (r.type as string)
            : "ai";
        const mapped = mapLangchainTypeToAgentscope(rawType);
        if (mapped.role === "user" || mapped.role === "system") continue;
        const id =
          typeof r.id === "string" && r.id
            ? (r.id as string)
            : `final-${next.length}`;
        next.push({
          object: "message",
          id,
          role: mapped.role,
          type: mapped.type,
          status: "completed",
          content: toAgentScopeContent(r.content, "completed"),
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
      return { object: "response", status: "in_progress", output: [] };
    }

    // Streaming token chunk: `event: messages-tuple` with {type, content, id}.
    if (data && typeof data.type === "string") {
      const mapped = mapLangchainTypeToAgentscope(data.type as string);
      // Skip user echoes – they're already rendered by the request card.
      if (mapped.role === "user" || mapped.role === "system") {
        return { object: "response", status: "in_progress", output: [] };
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
        return { object: "response", status: "in_progress", output: [] };
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
      return msg;
    }

    // Anything else (pure metadata, heartbeats) — keep the spinner alive.
    return { object: "response", status: "in_progress", output: [] };
  };
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
