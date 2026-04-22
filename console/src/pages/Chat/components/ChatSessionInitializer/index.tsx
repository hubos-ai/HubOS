import React, { useEffect, useMemo, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useChatAnywhereSessionsState } from "@agentscope-ai/chat";

/**
 * URL chatId → context currentSessionId (one direction of bidirectional sync).
 *
 * Only reacts to URL or session list changes. currentSessionId is read via ref
 * to avoid triggering the effect when the context changes from the other direction
 * (context → URL via onSessionSelected), which would cause circular re-loads.
 *
 * Matching policy
 * ---------------
 *
 * Sessions carry both a library-visible `id` (kept stable across the life of a
 * conversation, so the library's streaming pipeline is not interrupted) and a
 * backend UUID stored on `realId`. For freshly-created chats the visible `id`
 * is a local timestamp while `realId` is populated asynchronously once the
 * backend resolves it. The URL may contain either value depending on timing
 * (`/chat/<timestamp>` during the first turn, `/chat/<uuid>` once resolved).
 *
 * We therefore match on *either* `id` or `realId`. Previously we matched only
 * on `id`, which meant every time the library fired `syncSessionMessages`
 * during a streaming reply, the `sessions` reference would flip and this
 * effect would re-run; when the URL held the backend UUID but the stored
 * session still held `id = timestamp`, `matching` came back undefined and the
 * code navigated back to `/chat`. The outer `ChatPage` useEffect then tore
 * down `<AgentScopeRuntimeWebUI>` (via `routeSessionValidated`), causing a
 * visible full-surface flicker on every chunk.
 *
 * The navigate-to-`/chat` fallback is deliberately removed: the outer
 * `ChatPage` already validates the URL against the session list and the
 * backend, and is the proper owner of that bail-out. Doing it here too
 * created a race between the two during streaming.
 */
const ChatSessionInitializer: React.FC = () => {
  const location = useLocation();
  const chatId = useMemo(() => {
    const match = location.pathname.match(/^\/chat\/(.+)$/);
    return match?.[1];
  }, [location.pathname]);

  const { sessions, currentSessionId, setCurrentSessionId } =
    useChatAnywhereSessionsState();

  const currentSessionIdRef = useRef(currentSessionId);
  currentSessionIdRef.current = currentSessionId;

  useEffect(() => {
    if (!chatId || !sessions.length) return;
    const matching = sessions.find((s) => {
      if (s.id === chatId) return true;
      const realId = (s as { realId?: string | null }).realId;
      return typeof realId === "string" && realId === chatId;
    });
    if (matching && currentSessionIdRef.current !== matching.id) {
      setCurrentSessionId(matching.id);
    }
    // Intentionally exclude currentSessionId from deps: only react to URL / session list changes.
    // currentSessionId is read via ref to avoid circular triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId, sessions, setCurrentSessionId]);

  return null;
};

export default ChatSessionInitializer;
