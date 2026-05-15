import {
  AgentScopeRuntimeWebUI,
  IAgentScopeRuntimeWebUIOptions,
  type IAgentScopeRuntimeWebUIRef,
} from "@agentscope-ai/chat";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  lazy,
  Suspense,
} from "react";
import { Button, Modal, Result, Tooltip } from "antd";
import { useAppMessage } from "../../hooks/useAppMessage";
import { ExclamationCircleOutlined, SettingOutlined } from "@ant-design/icons";
import { SparkCopyLine, SparkAttachmentLine } from "@agentscope-ai/icons";
const ChatTaskPanel = lazy(() => import("./ChatTaskPanel"));
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import sessionApi, { clearAllPendingUserMessages } from "./sessionApi";
import defaultConfig, { getDefaultConfig } from "./OptionsPanel/defaultConfig";
import { chatApi } from "../../api/modules/chat";
import {
  runControlApi,
  findControllableRun,
} from "../../api/modules/runControl";
import { getApiUrl } from "../../api/config";
import { buildAuthHeaders } from "../../api/authHeaders";
import { providerApi } from "../../api/modules/provider";
import type { ProviderInfo, ModelInfo } from "../../api/types";
import ModelSelector from "./ModelSelector";
import { useTheme } from "../../contexts/ThemeContext";
import { useAgentStore } from "../../stores/agentStore";
import { useChatAnywhereInput } from "@agentscope-ai/chat";
import styles from "./index.module.less";
import { IconButton } from "@agentscope-ai/design";
import ChatActionGroup from "./components/ChatActionGroup";
import ChatHeaderTitle from "./components/ChatHeaderTitle";
import ChatSessionInitializer from "./components/ChatSessionInitializer";
import SelectiveTextCard from "./components/SelectiveTextCard";
import StatusToolCard from "./components/StatusToolCard";
import { SLASH_COMMANDS } from "./slashCommands";
import {
  toDisplayUrl,
  copyText,
  extractCopyableText,
  buildModelError,
  normalizeContentUrls,
  createSseChunkParser,
  extractUserMessageText,
  appendRuntimeNotice,
  type CopyableResponse,
  type RuntimeLoadingBridgeApi,
  type SseChunkParser,
} from "./utils";

const CHAT_ATTACHMENT_MAX_MB = 10;
// Idle-gap timeout for the chat SSE stream. The previous 20s ceiling was
// too aggressive: when the GM invokes `coordinate_workflow` / long-running
// tool calls, the parent stream can stay silent for 60-120s while sub-agents
// execute in sequence. Bump to 3 minutes of idle to cover typical multi-agent
// DAG runs; genuinely stuck streams still recover, just a bit later.
const CHAT_STREAM_IDLE_TIMEOUT_MS = 180000;

interface SessionInfo {
  session_id?: string;
  user_id?: string;
  channel?: string;
}

interface CustomWindow extends Window {
  currentSessionId?: string;
  currentUserId?: string;
  currentChannel?: string;
}

declare const window: CustomWindow;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_USER_ID = "default";
const DEFAULT_CHANNEL = "console";

// ---------------------------------------------------------------------------
// Custom hooks
// ---------------------------------------------------------------------------

/** Handle IME composition events to prevent premature Enter key submission. */
function useIMEComposition(isChatActive: () => boolean) {
  const isComposingRef = useRef(false);

  useEffect(() => {
    const handleCompositionStart = () => {
      if (!isChatActive()) return;
      isComposingRef.current = true;
    };

    const handleCompositionEnd = () => {
      if (!isChatActive()) return;
      // Use a slightly longer delay for Safari on macOS, which fires keydown
      // after compositionend within the same event loop tick.
      setTimeout(() => {
        isComposingRef.current = false;
      }, 200);
    };

    const suppressImeEnter = (e: KeyboardEvent) => {
      if (!isChatActive()) return;
      const target = e.target as HTMLElement;
      if (target?.tagName === "TEXTAREA" && e.key === "Enter" && !e.shiftKey) {
        // e.isComposing is the standard flag; isComposingRef covers the
        // post-compositionend grace period needed by Safari.
        if (isComposingRef.current || (e as any).isComposing) {
          e.stopPropagation();
          e.stopImmediatePropagation();
          e.preventDefault();
          return false;
        }
      }
    };

    document.addEventListener("compositionstart", handleCompositionStart, true);
    document.addEventListener("compositionend", handleCompositionEnd, true);
    // Listen on both keydown (Safari) and keypress (legacy) in capture phase.
    document.addEventListener("keydown", suppressImeEnter, true);
    document.addEventListener("keypress", suppressImeEnter, true);

    return () => {
      document.removeEventListener(
        "compositionstart",
        handleCompositionStart,
        true,
      );
      document.removeEventListener(
        "compositionend",
        handleCompositionEnd,
        true,
      );
      document.removeEventListener("keydown", suppressImeEnter, true);
      document.removeEventListener("keypress", suppressImeEnter, true);
    };
  }, [isChatActive]);

  return isComposingRef;
}

// ---------------------------------------------------------------------------
// Slash command overlay
// ---------------------------------------------------------------------------

/**
 * Set the textarea value in a way React / AgentScope can detect.
 * Uses the native setter so React's synthetic system picks up the change,
 * then dispatches a bubbling "input" event and repositions the caret.
 */
function setTextareaValue(
  ta: HTMLTextAreaElement,
  value: string,
  caret: number,
) {
  const nativeSetter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  if (nativeSetter) {
    nativeSetter.call(ta, value);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }
  ta.setSelectionRange(caret, caret);
}

/** Fetch and track multimodal capabilities for the active model. */
function useMultimodalCapabilities(
  refreshKey: number,
  locationPathname: string,
  isChatActive: () => boolean,
  selectedAgent: string,
) {
  const [multimodalCaps, setMultimodalCaps] = useState<{
    supportsMultimodal: boolean;
    supportsImage: boolean;
    supportsVideo: boolean;
  }>({ supportsMultimodal: false, supportsImage: false, supportsVideo: false });

  // Cache whether a valid model is configured — customFetch reads this
  // synchronously instead of making a network call on every message.
  const hasValidModelRef = useRef(false);

  const fetchMultimodalCaps = useCallback(async () => {
    try {
      const [providers, activeModels] = await Promise.all([
        providerApi.listProviders(),
        providerApi.getActiveModels({
          scope: "effective",
          agent_id: selectedAgent,
        }),
      ]);
      const activeProviderId = activeModels?.active_llm?.provider_id;
      const activeModelId = activeModels?.active_llm?.model;
      if (!activeProviderId || !activeModelId) {
        hasValidModelRef.current = false;
        setMultimodalCaps({
          supportsMultimodal: false,
          supportsImage: false,
          supportsVideo: false,
        });
        return;
      }
      hasValidModelRef.current = true;
      const provider = (providers as ProviderInfo[]).find(
        (p) => p.id === activeProviderId,
      );
      if (!provider) {
        hasValidModelRef.current = false;
        setMultimodalCaps({
          supportsMultimodal: false,
          supportsImage: false,
          supportsVideo: false,
        });
        return;
      }
      const allModels: ModelInfo[] = [
        ...(provider.models ?? []),
        ...(provider.extra_models ?? []),
      ];
      const model = allModels.find((m) => m.id === activeModelId);
      setMultimodalCaps({
        supportsMultimodal: model?.supports_multimodal ?? false,
        supportsImage: model?.supports_image ?? false,
        supportsVideo: model?.supports_video ?? false,
      });
    } catch {
      hasValidModelRef.current = false;
      setMultimodalCaps({
        supportsMultimodal: false,
        supportsImage: false,
        supportsVideo: false,
      });
    }
  }, [selectedAgent]);

  // Fetch caps on mount and whenever refreshKey changes
  useEffect(() => {
    fetchMultimodalCaps();
  }, [fetchMultimodalCaps, refreshKey]);

  // Also poll caps when navigating back to chat
  useEffect(() => {
    if (isChatActive()) {
      fetchMultimodalCaps();
    }
  }, [locationPathname, fetchMultimodalCaps, isChatActive]);

  // Listen for model-switched event from ModelSelector
  useEffect(() => {
    const handler = () => {
      fetchMultimodalCaps();
    };
    window.addEventListener("model-switched", handler);
    return () => window.removeEventListener("model-switched", handler);
  }, [fetchMultimodalCaps]);

  return { caps: multimodalCaps, hasValidModelRef };
}

/**
 * RuntimeLoadingBridge — separates AgentScope's loading from HubOS's running.
 *
 * When AgentScope internally calls setLoading(true) (SSE stream started),
 * we capture it as HubOS "running" state but immediately reset the context
 * loading to false. This keeps the Sender component fully interactive
 * (textarea + send button always available) while we manage the stop/guidance
 * UI ourselves.
 *
 * Two-layer state:
 * - AgentScope context loading: always false → Sender never blocks submission
 * - HubOS runtimeLoading (via onLoadingChange): true while work is happening
 *
 * Ending the run:
 * - SSE stream ends → customFetch calls bridgeRef.setLoading(false)
 * - User stops → stopCurrentRun calls bridgeRef.setLoading(false)
 * - NOT triggered by AgentScope's internal setLoading(false) to avoid races
 */
function RuntimeLoadingBridge({
  bridgeRef,
  onLoadingChange,
}: {
  bridgeRef: { current: RuntimeLoadingBridgeApi | null };
  onLoadingChange?: (loading: boolean) => void;
}) {
  const { loading, setLoading, getLoading } = useChatAnywhereInput(
    (value) =>
      ({
        loading: value.loading,
        setLoading: value.setLoading,
        getLoading: value.getLoading,
      }) as RuntimeLoadingBridgeApi & { loading?: boolean | string },
  );

  const mountedRef = useRef(true);
  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  // When AgentScope sets loading=true, treat it as "run started" signal.
  // Record the state for HubOS UI, then immediately reset context loading
  // to false so the Sender stays interactive.
  useEffect(() => {
    const isLoading = Boolean(loading);
    if (isLoading) {
      onLoadingChange?.(true);
      // queueMicrotask avoids setState-during-render and batches the reset
      // within the same task so the Sender never sees loading=true for long.
      queueMicrotask(() => {
        if (mountedRef.current) setLoading?.(false);
      });
    }
    // Deliberately NOT calling onLoadingChange(false) here — loading=false
    // from AgentScope should NOT end the HubOS running state. Only our own
    // code (customFetch SSE end, stopCurrentRun) resets runtimeLoading via
    // bridgeRef.current.setLoading(false).
  }, [loading, onLoadingChange, setLoading]);

  useEffect(() => {
    if (!setLoading || !getLoading) {
      bridgeRef.current = null;
      return;
    }

    bridgeRef.current = {
      // Called by HubOS code (customFetch, stopCurrentRun) to signal "work done".
      // This resets HubOS runtimeLoading — does NOT affect AgentScope context.
      setLoading: (value: boolean | string) => {
        if (!value) {
          onLoadingChange?.(false);
        }
      },
      getLoading: () => false,
    };

    return () => {
      bridgeRef.current = null;
    };
  }, [onLoadingChange, bridgeRef]);

  return null;
}

export default function ChatPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { isDark } = useTheme();
  const chatId = useMemo(() => {
    const match = location.pathname.match(/^\/chat\/(.+)$/);
    return match?.[1];
  }, [location.pathname]);
  const [showModelPrompt, setShowModelPrompt] = useState(false);
  const [routeSessionValidated, setRouteSessionValidated] = useState(
    () => !chatId,
  );
  const { selectedAgent } = useAgentStore();
  const [refreshKey, setRefreshKey] = useState(0);
  const runtimeLoadingBridgeRef = useRef<RuntimeLoadingBridgeApi | null>(null);
  const { message } = useAppMessage();
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);
  const [pendingGuidanceText, setPendingGuidanceText] = useState("");
  // Ref (not state): AgentScope Input freezes beforeSubmit via useCallback([],…)
  // so beforeSubmit can only read a ref, never a stale state closure.
  const runtimeLoadingRef = useRef(false);
  const currentAbortRef = useRef<AbortController | null>(null);

  // Stream lifecycle tracking — prevents old SSE chunks from bleeding into
  // new guidance/submit streams by ensuring the old reader is fully drained
  // before starting a new one.
  const activeStreamDoneRef = useRef<Promise<void> | null>(null);
  const resolveActiveStreamDoneRef = useRef<(() => void) | null>(null);
  // Monotonically increasing ID — stale readers check this and stop enqueuing.
  const latestRequestIdRef = useRef(0);
  // Guard: only one guidance submit in-flight at a time.
  const submittingGuidanceRef = useRef(false);

  // Stream lifecycle helpers (stable callbacks — only access refs).
  const markStreamStarted = useCallback(() => {
    let resolve!: () => void;
    const promise = new Promise<void>((r) => {
      resolve = r;
    });
    activeStreamDoneRef.current = promise;
    resolveActiveStreamDoneRef.current = resolve;
  }, []);

  const markStreamDone = useCallback(() => {
    resolveActiveStreamDoneRef.current?.();
    activeStreamDoneRef.current = null;
    resolveActiveStreamDoneRef.current = null;
  }, []);

  const waitActiveStreamDone = useCallback(async (timeoutMs = 800) => {
    const promise = activeStreamDoneRef.current;
    if (!promise) return;
    await Promise.race([
      promise,
      new Promise<void>((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
  }, []);

  const isChatActiveRef = useRef(false);
  isChatActiveRef.current =
    location.pathname === "/" || location.pathname.startsWith("/chat");

  const isChatActive = useCallback(() => isChatActiveRef.current, []);

  // Use custom hooks for better separation of concerns
  useIMEComposition(isChatActive);
  const { caps: multimodalCaps, hasValidModelRef } = useMultimodalCapabilities(
    refreshKey,
    location.pathname,
    isChatActive,
    selectedAgent,
  );

  const lastSessionIdRef = useRef<string | null>(null);
  /** Tracks the stale auto-selected session ID that was skipped on init, so we can suppress its late-arriving onSessionSelected callback. */
  const staleAutoSelectedIdRef = useRef<string | null>(null);
  const chatIdRef = useRef(chatId);
  const navigateRef = useRef(navigate);
  const chatRef = useRef<IAgentScopeRuntimeWebUIRef>(null);
  chatIdRef.current = chatId;
  navigateRef.current = navigate;

  useEffect(() => {
    let cancelled = false;

    if (!chatId) {
      // Entering /chat without a chatId: the user landed on the chat surface
      // without a specific conversation. Drop any cached pending-user-message
      // entries so the library's auto-selection of sessions[0] cannot replay
      // a phantom user card (the "looks like auto-sending" symptom).
      clearAllPendingUserMessages();
      setRouteSessionValidated(true);
      return;
    }

    // Fast path: if the incoming chatId is already known to sessionApi's
    // in-memory list (either as the visible `id` or as the backend `realId`
    // that just got promoted onto the URL after the first turn), skip the
    // "invalidated → revalidate" transition entirely. Going through that
    // transition would unmount `<AgentScopeRuntimeWebUI>` mid-stream,
    // aborting the SSE reader and causing a visible full-surface flicker.
    if (sessionApi.isKnownSessionId(chatId)) {
      setRouteSessionValidated(true);
      return;
    }

    setRouteSessionValidated(false);

    // Local-timestamp IDs (pure digits) are client-only placeholders minted by
    // sessionApi.createSession before the first message has reached the backend.
    // Hitting `GET /api/chats/<digits>` for those always 404s and, combined
    // with the library's auto re-selection, produces a navigate ↔ 404 loop.
    // Treat them as valid locally and let sessionApi resolve the real id.
    const isLocalTimestampId = /^\d+$/.test(chatId);
    if (isLocalTimestampId) {
      void sessionApi.getSessionList().then((sessions) => {
        if (cancelled) return;
        const known = sessions.some(
          (session) =>
            session.id === chatId ||
            (session as { realId?: string | null }).realId === chatId,
        );
        if (!known) {
          // Unknown local id in the URL (stale refresh / copy-pasted link):
          // clear the route back to /chat rather than calling the backend.
          clearAllPendingUserMessages();
          navigate("/chat", { replace: true });
          return;
        }
        setRouteSessionValidated(true);
      });
      return () => {
        cancelled = true;
      };
    }

    // Check the session list FIRST to avoid a backend 404 for IDs that are
    // simply not ours (stale bookmarks, copy-pasted URLs, post-delete refresh).
    // Only if the session IS in the list do we trust the route as valid.
    // _doGetSession will handle the 404 case if the backend lost the record.
    void sessionApi.getSessionList().then(async (sessions) => {
      if (cancelled) return;
      const exists = sessions.some(
        (session) =>
          session.id === chatId ||
          (session as { realId?: string | null }).realId === chatId,
      );
      if (!exists) {
        // chatId not in our session list — clear phantom cache and redirect.
        clearAllPendingUserMessages();
        navigate("/chat", { replace: true });
        return;
      }
      // Session is known-good; mark route as validated. If the backend has
      // since deleted the record, _doGetSession will purge + navigate(/chat).
      setRouteSessionValidated(true);
    });

    return () => {
      cancelled = true;
    };
  }, [chatId, navigate]);

  // Tell sessionApi which session to put first in getSessionList, so the library's
  // useMount auto-selects the correct session without an extra getSession round-trip.
  if (chatId) {
    if (sessionApi.preferredChatId !== chatId) {
      sessionApi.preferredChatId = chatId;
    }
  } else if (sessionApi.preferredChatId !== null) {
    sessionApi.preferredChatId = null;
  }

  // Register session API event callbacks for URL synchronization

  useEffect(() => {
    sessionApi.onSessionIdResolved = (_tempId, realId) => {
      if (!isChatActiveRef.current) return;
      // Update URL to the real backend UUID once it has been resolved.
      // The first parameter is the local timestamp placeholder (ignored here);
      // the second is the canonical backend UUID we want in the URL.
      lastSessionIdRef.current = realId;
      navigateRef.current(`/chat/${realId}`, { replace: true });
    };

    sessionApi.onSessionRemoved = (removedId) => {
      if (!isChatActiveRef.current) return;
      // Clear URL when current session is removed
      // Check if removed session matches current session (by realId or sessionId)
      const currentRealId = sessionApi.getRealIdForSession(
        chatIdRef.current || "",
      );
      if (chatIdRef.current === removedId || currentRealId === removedId) {
        lastSessionIdRef.current = null;
        navigateRef.current("/chat", { replace: true });
      }
    };

    sessionApi.onSessionSelected = (
      sessionId: string | null | undefined,
      realId: string | null,
    ) => {
      if (!isChatActiveRef.current) return;
      // Update URL when session is selected and different from current
      const targetId = realId || sessionId;
      if (!targetId) return;

      // If a preferred chatId from the URL exists and no navigation has happened yet,
      // skip the library's initial auto-selection (always first session).
      // ChatSessionInitializer will apply the correct selection afterward.
      if (
        chatIdRef.current &&
        lastSessionIdRef.current === null &&
        targetId !== chatIdRef.current
      ) {
        lastSessionIdRef.current = targetId;
        // Record the stale ID so its delayed getSession callback is also suppressed.
        staleAutoSelectedIdRef.current = targetId;
        return;
      }

      // Suppress the stale getSession callback that arrives after the correct session loads.
      if (
        staleAutoSelectedIdRef.current &&
        staleAutoSelectedIdRef.current === targetId
      ) {
        staleAutoSelectedIdRef.current = null;
        return;
      }

      if (targetId !== lastSessionIdRef.current) {
        lastSessionIdRef.current = targetId;
        navigateRef.current(`/chat/${targetId}`, { replace: true });
      }
    };

    sessionApi.onSessionCreated = () => {
      if (!isChatActiveRef.current) return;
      // Clear URL when creating new session, wait for realId resolution to update
      lastSessionIdRef.current = null;
      navigateRef.current("/chat", { replace: true });
    };

    return () => {
      sessionApi.onSessionIdResolved = null;
      sessionApi.onSessionRemoved = null;
      sessionApi.onSessionSelected = null;
      sessionApi.onSessionCreated = null;
    };
  }, []);

  // Setup multimodal capabilities tracking via custom hook

  // Refresh chat when selectedAgent changes
  const prevSelectedAgentRef = useRef(selectedAgent);
  useEffect(() => {
    // Only refresh if selectedAgent actually changed (not initial mount)
    if (
      prevSelectedAgentRef.current !== selectedAgent &&
      prevSelectedAgentRef.current !== undefined
    ) {
      // Force re-render by updating refresh key
      setRefreshKey((prev) => prev + 1);
    }
    prevSelectedAgentRef.current = selectedAgent;
  }, [selectedAgent]);

  const copyResponse = useCallback(
    async (response: CopyableResponse) => {
      try {
        await copyText(extractCopyableText(response));
        message.success(t("common.copied"));
      } catch {
        message.error(t("common.copyFailed"));
      }
    },
    [t],
  );

  const getVisibleSessionId = useCallback(
    () => window.currentSessionId || chatIdRef.current || "",
    [],
  );

  const findCurrentControllableRun = useCallback(async () => {
    const visibleSessionId = getVisibleSessionId();
    if (visibleSessionId) {
      const activeRuns = await runControlApi.getActiveRuns(visibleSessionId);
      const target = findControllableRun(activeRuns.runs ?? []);
      if (target) return target;
    }
    // Fallback for console/tool runs whose backend session id can differ from
    // the AgentScope visible tab id. This keeps guidance available while a
    // task is visibly running instead of leaving only the native stop button.
    const allRuns = await runControlApi.listRuns({ activeOnly: true });
    return findControllableRun(allRuns.runs ?? []);
  }, [getVisibleSessionId]);

  const stopCurrentRun = useCallback(async () => {
    const visibleSessionId = getVisibleSessionId();
    const backendChatId =
      sessionApi.getRealIdForSession(visibleSessionId) ??
      chatIdRef.current ??
      visibleSessionId;
    if (!backendChatId) return false;

    // Try RunControl first: find best controllable run for session
    try {
      const target = await findCurrentControllableRun();
      if (target) {
        await runControlApi.cancelRun(target.run_id);
        runtimeLoadingBridgeRef.current?.setLoading?.(false);
        return true;
      }
    } catch {
      // RunControl unavailable — fallback below
    }

    // Fallback: legacy stopChat (only when RunControl has no active runs)
    try {
      await chatApi.stopChat(backendChatId);
      runtimeLoadingBridgeRef.current?.setLoading?.(false);
      return true;
    } catch (err) {
      console.error("Failed to stop current run:", err);
      return false;
    }
  }, [findCurrentControllableRun, getVisibleSessionId]);

  // One stateful SSE parser per Chat mount. The closure accumulates streamed
  // assistant tokens and resets on each `metadata: started` event from the
  // backend, so successive runs do not bleed into one another.
  const sseChunkParser = useMemo<SseChunkParser>(
    () => createSseChunkParser(),
    [],
  );

  const guidePendingText = useCallback(async () => {
    const text = pendingGuidanceText.trim();
    if (!text || submittingGuidanceRef.current) return;
    submittingGuidanceRef.current = true;

    try {
      // 1. Abort the active SSE stream immediately
      currentAbortRef.current?.abort();
      currentAbortRef.current = null;

      // 2. Wait for old stream to fully drain so stale chunks cannot
      //    write to the new currentQARef.current.response.
      await waitActiveStreamDone(800);

      // 3. Reset parser — clear all accumulated state from previous stream
      sseChunkParser.reset();

      // 4. Reset UI state (user sees instant feedback)
      runtimeLoadingRef.current = false;
      runtimeLoadingBridgeRef.current?.setLoading?.(false);
      setPendingGuidanceText("");

      // 5. Call RunControl guidance API to get guidance_ack
      let guidanceAck: string | undefined;
      let guidedFromRunId: string | undefined;
      try {
        const target = await findCurrentControllableRun();
        if (target) {
          guidedFromRunId = target.run_id;
          const resp = await runControlApi.guidance(target.run_id, text);
          guidanceAck = resp.guidance_ack;
        }
      } catch {
        // RunControl unavailable — degrade to guidance without ack
      }

      // 6. Inject guidance notice into chat timeline
      appendRuntimeNotice(chatRef, "↪️ 已收到引导，正在按新方向继续");

      // 7. Submit guidance. The backend sees runtime_guidance=true and uses
      //    TaskTracker.force_new to atomically cancel the previous producer
      //    without replaying old buffers.
      chatRef.current?.input.submit({
        query: text,
        biz_params: {
          runtime_guidance: true,
          guidance_text: text,
          ...(guidanceAck ? { guidance_ack: guidanceAck } : {}),
          ...(guidedFromRunId ? { guided_from_run_id: guidedFromRunId } : {}),
        } as Record<string, unknown>,
      });
    } finally {
      submittingGuidanceRef.current = false;
    }
  }, [
    pendingGuidanceText,
    findCurrentControllableRun,
    sseChunkParser,
    waitActiveStreamDone,
  ]);

  const discardPendingGuidance = useCallback(() => {
    setPendingGuidanceText("");
  }, []);

  const terminateCurrentRun = useCallback(async () => {
    // Abort the active SSE stream immediately
    currentAbortRef.current?.abort();
    currentAbortRef.current = null;

    // Wait for old stream to fully drain so stale chunks cannot pollute UI
    await waitActiveStreamDone(800);

    // Reset parser — clear accumulated state from current stream
    sseChunkParser.reset();

    const stopped = await stopCurrentRun();
    if (stopped) {
      setPendingGuidanceText("");
      appendRuntimeNotice(chatRef, "⏹️ 任务已终止");
    }
  }, [stopCurrentRun, sseChunkParser, waitActiveStreamDone]);

  const customFetch = useCallback(
    async (data: {
      input?: Array<Record<string, unknown>>;
      biz_params?: Record<string, unknown>;
      signal?: AbortSignal;
    }): Promise<Response> => {
      const stopRuntimeLoading = () => {
        try {
          runtimeLoadingBridgeRef.current?.setLoading?.(false);
        } catch {
          // Best-effort only. Runtime API is owned by the chat dependency.
        }
      };

      const requestController = new AbortController();
      currentAbortRef.current = requestController;
      // Stale-request guard: each invocation gets a unique ID. If a newer
      // invocation arrives (guidance, new submit), the old pump stops enqueuing.
      const localRequestId = ++latestRequestIdRef.current;
      const abortRequest = () => requestController.abort();

      if (data.signal) {
        if (data.signal.aborted) {
          abortRequest();
        } else {
          data.signal.addEventListener("abort", abortRequest, { once: true });
        }
      }

      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        ...buildAuthHeaders(),
      };

      if (!hasValidModelRef.current) {
        setShowModelPrompt(true);
        return buildModelError();
      }

      const { input = [], biz_params } = data;
      const session: SessionInfo = input[input.length - 1]?.session || {};
      const lastInput = input.slice(-1);
      const lastMsg = lastInput[0];
      const rewrittenInput =
        lastMsg?.content && Array.isArray(lastMsg.content)
          ? [
              {
                ...lastMsg,
                content: lastMsg.content.map(normalizeContentUrls),
              },
            ]
          : lastInput;

      const requestBody = {
        input: rewrittenInput,
        session_id: window.currentSessionId || session?.session_id || "",
        user_id: window.currentUserId || session?.user_id || DEFAULT_USER_ID,
        channel: window.currentChannel || session?.channel || DEFAULT_CHANNEL,
        stream: true,
        biz_params: biz_params || {},
      };

      // For non-guidance submits, reset the parser so old accumulated state
      // from a previous stream does not bleed into the new one.
      // Guidance submits already call reset in guidePendingText.
      if (!biz_params?.runtime_guidance) {
        sseChunkParser.reset();
      }

      const backendChatId =
        sessionApi.getRealIdForSession(requestBody.session_id) ??
        chatIdRef.current ??
        requestBody.session_id;
      const userText = rewrittenInput
        .filter((m: any) => m.role === "user")
        .map(extractUserMessageText)
        .join("\n")
        .trim();
      if (userText) {
        // Store under ALL possible session IDs so the pending user message
        // can be recovered regardless of which ID is used on reload.
        const allIds = new Set<string>();
        if (backendChatId) allIds.add(backendChatId);
        if (requestBody.session_id) allIds.add(requestBody.session_id);
        if (chatIdRef.current) allIds.add(chatIdRef.current);
        if (window.currentSessionId) allIds.add(window.currentSessionId);
        sessionApi.setLastUserMessage([...allIds], userText);
      }

      const response = await fetch(getApiUrl("/console/chat"), {
        method: "POST",
        headers,
        body: JSON.stringify(requestBody),
        signal: requestController.signal,
      });

      if (
        !response.body ||
        !response.headers.get("content-type")?.includes("text/event-stream")
      ) {
        return response;
      }

      markStreamStarted();

      const reader = response.body.getReader();

      const wrappedBody = new ReadableStream<Uint8Array>({
        start(controller) {
          let timeoutId: ReturnType<typeof setTimeout> | null = null;
          let sawTerminalEvent = false;
          const decoder = new TextDecoder();
          const encoder = new TextEncoder();

          const clearTimer = () => {
            if (timeoutId) {
              clearTimeout(timeoutId);
              timeoutId = null;
            }
          };

          const armTimer = () => {
            clearTimer();
            timeoutId = setTimeout(() => {
              stopRuntimeLoading();
              message.error(t("chat.requestTimeout", "Reply timed out"));
              requestController.abort();
              reader.cancel("chat-stream-timeout").catch(() => {});
              markStreamDone();
              controller.error(new Error("Chat stream timed out"));
            }, CHAT_STREAM_IDLE_TIMEOUT_MS);
          };

          const pump = () => {
            armTimer();
            reader
              .read()
              .then(({ done, value }) => {
                if (done) {
                  clearTimer();
                  currentAbortRef.current = null;
                  // Some backend/proxy paths close the SSE stream after the
                  // last message without delivering the terminal
                  // `event: end\ndata: null` marker. AgentScope only flips
                  // the response to "finished" after parsing that marker, and
                  // the copy footer is hidden while the response is generating.
                  if (!sawTerminalEvent) {
                    controller.enqueue(
                      encoder.encode("event: end\ndata: null\n\n"),
                    );
                  }
                  // Immediate release: SSE stream ended → task complete.
                  // This lets beforeSubmit see runtimeLoading=false instantly,
                  // without waiting for the next heartbeat cycle.
                  runtimeLoadingRef.current = false;
                  stopRuntimeLoading();
                  markStreamDone();
                  controller.close();
                  return;
                }
                // Stale request guard — if a newer fetch has started, stop
                // feeding old data to AgentScope's response builder.
                if (localRequestId !== latestRequestIdRef.current) {
                  clearTimer();
                  currentAbortRef.current = null;
                  markStreamDone();
                  controller.close();
                  return;
                }
                armTimer();
                const text = decoder.decode(value, { stream: true });
                if (
                  text.includes("event: end") ||
                  text.includes("data: null")
                ) {
                  sawTerminalEvent = true;
                }
                controller.enqueue(value);
                pump();
              })
              .catch((error) => {
                clearTimer();
                currentAbortRef.current = null;
                // Release on error too — no point keeping runtimeLoading true.
                runtimeLoadingRef.current = false;
                stopRuntimeLoading();
                markStreamDone();
                controller.error(error);
              });
          };

          pump();
        },
        cancel(reason) {
          markStreamDone();
          return reader.cancel(reason);
        },
      });

      return new Response(wrappedBody, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    },
    [
      message,
      selectedAgent,
      sseChunkParser,
      t,
      markStreamStarted,
      markStreamDone,
    ],
  );

  const handleFileUpload = useCallback(
    async (options: {
      file: File;
      onSuccess: (body: { url?: string; thumbUrl?: string }) => void;
      onError?: (e: Error) => void;
      onProgress?: (e: { percent?: number }) => void;
    }) => {
      const { file, onSuccess, onError, onProgress } = options;
      try {
        // Warn when model has no multimodal support
        if (!multimodalCaps.supportsMultimodal) {
          message.warning(t("chat.attachments.multimodalWarning"));
        } else if (
          multimodalCaps.supportsImage &&
          !multimodalCaps.supportsVideo &&
          !file.type.startsWith("image/")
        ) {
          // Warn (not block) when only image is supported
          message.warning(t("chat.attachments.imageOnlyWarning"));
        }
        const sizeMb = file.size / 1024 / 1024;
        const isWithinLimit = sizeMb < CHAT_ATTACHMENT_MAX_MB;

        if (!isWithinLimit) {
          message.error(
            t("chat.attachments.fileSizeExceeded", {
              limit: CHAT_ATTACHMENT_MAX_MB,
              size: sizeMb.toFixed(2),
            }),
          );
          onError?.(new Error(`File size exceeds ${CHAT_ATTACHMENT_MAX_MB}MB`));
          return;
        }

        const res = await chatApi.uploadFile(file);
        onProgress?.({ percent: 100 });
        onSuccess({ url: chatApi.filePreviewUrl(res.url) });
      } catch (e) {
        onError?.(e instanceof Error ? e : new Error(String(e)));
      }
    },
    [multimodalCaps, t],
  );

  const options = useMemo(() => {
    const i18nConfig = getDefaultConfig(t);

    return {
      ...i18nConfig,
      theme: {
        ...defaultConfig.theme,
        darkMode: isDark,
        leftHeader: {
          ...defaultConfig.theme.leftHeader,
        },
        rightHeader: (
          <>
            <ChatSessionInitializer />
            <RuntimeLoadingBridge
              bridgeRef={runtimeLoadingBridgeRef}
              onLoadingChange={(loading: boolean) => {
                runtimeLoadingRef.current = loading;
              }}
            />
            <ChatHeaderTitle />
            <span style={{ flex: 1 }} />
            <ModelSelector />
            <Tooltip title={t("chatTask.toggleBtn", "Tasks")}>
              <Button
                size="small"
                type={taskPanelOpen ? "primary" : "default"}
                onClick={() => {
                  setTaskPanelOpen((open) => !open);
                }}
                style={{ marginLeft: 4 }}
              >
                {t("chatTask.toggleBtn", "Tasks")}
              </Button>
            </Tooltip>
            <ChatActionGroup />
          </>
        ),
      },
      welcome: {
        ...i18nConfig.welcome,
        nick: "HubOS",
        avatar: `${import.meta.env.BASE_URL}hubos-buddy.png?v=20260427`,
      },
      sender: {
        ...(i18nConfig as any)?.sender,
        beforeSubmit: async () => {
          // Read textarea from DOM (beforeSubmit receives no arguments per
          // library API).
          const textarea = document.querySelector<HTMLTextAreaElement>(
            [
              ".chat-anywhere-sender textarea",
              'textarea[class*="sender"]',
              'textarea[class*="chat-anywhere"]',
            ].join(", "),
          );
          const text = textarea?.value?.trim();

          // /stop during active run: invoke stopCurrentRun directly instead
          // of sending as a chat message (which would be queued behind the
          // running producer and never processed).
          if (text === "/stop" && runtimeLoadingRef.current) {
            if (textarea) {
              setTextareaValue(textarea, "", 0);
            }
            stopCurrentRun();
            return false;
          }

          if (!runtimeLoadingRef.current) return true;

          // Guard: if no active abort controller and no active stream, the
          // runtimeLoadingRef is stale (e.g. stream ended but ref wasn't
          // cleared yet). Allow normal submit.
          if (!currentAbortRef.current && !activeStreamDoneRef.current) {
            runtimeLoadingRef.current = false;
            return true;
          }

          // Agent generating — intercept.
          if (!text || text.startsWith("/")) return true;

          setPendingGuidanceText(text);

          // Clear textarea via native setter (React-compatible).
          if (textarea) {
            setTextareaValue(textarea, "", 0);
          }

          return false; // suppress AgentScope submit, input not cleared
        },
        allowSpeech: true,
        attachments: {
          trigger: function (props: any) {
            const tooltipKey = multimodalCaps.supportsMultimodal
              ? multimodalCaps.supportsImage && !multimodalCaps.supportsVideo
                ? "chat.attachments.tooltipImageOnly"
                : "chat.attachments.tooltip"
              : "chat.attachments.tooltipNoMultimodal";
            return (
              <Tooltip title={t(tooltipKey, { limit: CHAT_ATTACHMENT_MAX_MB })}>
                <IconButton
                  disabled={props?.disabled}
                  icon={<SparkAttachmentLine />}
                  bordered={false}
                />
              </Tooltip>
            );
          },
          accept: "*/*",
          customRequest: handleFileUpload,
        },
        placeholder: t("chat.inputPlaceholder"),
        suggestions: SLASH_COMMANDS.map((cmd) => ({
          value: cmd.command.replace(/^\//, ""),
          label: (
            <div className={styles.suggestionLabel}>
              <span className={styles.suggestionCommand}>{cmd.command}</span>
              <span className={styles.suggestionDescription}>
                {t(`${cmd.i18nKey}.description`)}
              </span>
            </div>
          ),
        })),
      },
      session: {
        multiple: true,
        hideBuiltInSessionList: true,
        api: sessionApi,
      },
      cards: {
        Text: SelectiveTextCard,
      },
      customToolRenderConfig: {
        "Context understanding": StatusToolCard,
        "Experience matching": StatusToolCard,
        "Knowledge injection": StatusToolCard,
      },
      api: {
        ...defaultConfig.api,
        fetch: customFetch,
        responseParser: sseChunkParser,
        replaceMediaURL: (url: string) => {
          return toDisplayUrl(url);
        },
        cancel(data: { session_id: string }) {
          const chatId =
            sessionApi.getRealIdForSession(data.session_id) ?? data.session_id;
          if (chatId) {
            chatApi.stopChat(chatId).catch((err) => {
              console.error("Failed to stop chat:", err);
            });
          }
        },
        async reconnect(data: { session_id: string; signal?: AbortSignal }) {
          const headers: Record<string, string> = {
            "Content-Type": "application/json",
            ...buildAuthHeaders(),
          };

          return fetch(getApiUrl("/console/chat"), {
            method: "POST",
            headers,
            body: JSON.stringify({
              reconnect: true,
              session_id: window.currentSessionId || data.session_id,
              user_id: window.currentUserId || DEFAULT_USER_ID,
              channel: window.currentChannel || DEFAULT_CHANNEL,
            }),
            signal: data.signal,
          });
        },
      },
      actions: {
        list: [
          {
            icon: (
              <span title={t("common.copy")}>
                <SparkCopyLine />
              </span>
            ),
            onClick: ({ data }: { data: CopyableResponse }) => {
              void copyResponse(data);
            },
          },
        ],
        replace: true,
      },
    } as unknown as IAgentScopeRuntimeWebUIOptions;
  }, [
    customFetch,
    copyResponse,
    handleFileUpload,
    t,
    isDark,
    multimodalCaps,
    taskPanelOpen,
    sseChunkParser,
  ]);

  return (
    <div className={styles.chatPageRoot}>
      <div
        style={{
          position: "relative",
          height: "100%",
          width: "100%",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div className={styles.chatMessagesArea}>
          {routeSessionValidated ? (
            <AgentScopeRuntimeWebUI
              ref={chatRef}
              key={refreshKey}
              options={options}
            />
          ) : null}
        </div>
        {/* Pending Guidance Card — positioned above the input, outside scroll area */}
        {pendingGuidanceText && (
          <div className={styles.pendingGuidanceCard}>
            <span className={styles.pendingGuidanceDot} />
            <span className={styles.pendingGuidanceText}>
              {pendingGuidanceText}
            </span>
            <div className={styles.pendingGuidanceActions}>
              <Button size="small" type="link" onClick={guidePendingText}>
                {t("chat.guidance.guide", "引导")}
              </Button>
              <Button size="small" type="link" onClick={terminateCurrentRun}>
                {t("chat.guidance.terminate", "终止")}
              </Button>
              <Button
                size="small"
                type="link"
                danger
                onClick={discardPendingGuidance}
              >
                {t("chat.guidance.discard", "丢弃")}
              </Button>
            </div>
          </div>
        )}
      </div>

      <Suspense fallback={null}>
        <ChatTaskPanel
          sessionId={window.currentSessionId || chatId || ""}
          open={taskPanelOpen}
          onClose={() => setTaskPanelOpen(false)}
        />
      </Suspense>

      <Modal
        open={showModelPrompt}
        closable={false}
        footer={null}
        width={480}
        styles={{
          content: isDark
            ? { background: "#1f1f1f", boxShadow: "0 8px 32px rgba(0,0,0,0.5)" }
            : undefined,
        }}
      >
        <Result
          icon={<ExclamationCircleOutlined style={{ color: "#faad14" }} />}
          title={
            <span
              style={{ color: isDark ? "rgba(255,255,255,0.88)" : undefined }}
            >
              {t("modelConfig.promptTitle")}
            </span>
          }
          subTitle={
            <span
              style={{ color: isDark ? "rgba(255,255,255,0.55)" : undefined }}
            >
              {t("modelConfig.promptMessage")}
            </span>
          }
          extra={[
            <Button key="skip" onClick={() => setShowModelPrompt(false)}>
              {t("modelConfig.skipButton")}
            </Button>,
            <Button
              key="configure"
              type="primary"
              icon={<SettingOutlined />}
              onClick={() => {
                setShowModelPrompt(false);
                navigate("/models");
              }}
            >
              {t("modelConfig.configureButton")}
            </Button>,
          ]}
        />
      </Modal>
    </div>
  );
}
