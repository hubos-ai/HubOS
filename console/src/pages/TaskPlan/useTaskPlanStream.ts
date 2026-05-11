// -*- coding: utf-8 -*-
/**
 * SSE stream hook for Task Plans.
 *
 * Uses fetch + ReadableStream with auth headers.
 * Auto-reconnects with exponential back-off.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getApiUrl } from "../../api/config";
import { buildAuthHeaders } from "../../api/authHeaders";
import type { PlanSSEEvent } from "../../api/types/taskPlan";

interface UseTaskPlanStreamOptions {
  enabled?: boolean;
  sessionId?: string;
}

interface UseTaskPlanStreamReturn {
  lastEvent: PlanSSEEvent | null;
  connected: boolean;
}

const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000] as const;

export function useTaskPlanStream(
  options: UseTaskPlanStreamOptions = {},
): UseTaskPlanStreamReturn {
  const { enabled = true, sessionId } = options;
  const [lastEvent, setLastEvent] = useState<PlanSSEEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const retryCountRef = useRef(0);
  const mountedRef = useRef(true);

  const connect = useCallback(async () => {
    if (!enabled || !mountedRef.current) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const params = sessionId
      ? `?session_id=${encodeURIComponent(sessionId)}`
      : "";
    const url = getApiUrl(`/task-plans/stream${params}`);

    try {
      const headers = buildAuthHeaders();
      headers.Accept = "text/event-stream";

      const response = await fetch(url, {
        method: "GET",
        headers,
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`SSE connect failed: ${response.status}`);
      }

      if (!mountedRef.current) return;
      setConnected(true);
      retryCountRef.current = 0;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (mountedRef.current) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";

        for (const part of parts) {
          const lines = part.split("\n");
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const payload = JSON.parse(line.slice(6)) as PlanSSEEvent;
                if (mountedRef.current) {
                  setLastEvent(payload);
                }
              } catch {
                // Ignore malformed JSON
              }
            }
          }
        }
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      if (err instanceof DOMException && err.name === "AbortError") return;
    } finally {
      if (mountedRef.current) {
        setConnected(false);
      }
    }

    if (mountedRef.current && enabled) {
      const delay =
        BACKOFF_MS[Math.min(retryCountRef.current, BACKOFF_MS.length - 1)];
      retryCountRef.current += 1;
      setTimeout(() => {
        if (mountedRef.current && enabled) {
          connect();
        }
      }, delay);
    }
  }, [enabled, sessionId]);

  useEffect(() => {
    mountedRef.current = true;
    if (enabled) {
      connect();
    }
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, [connect, enabled]);

  return { lastEvent, connected };
}
