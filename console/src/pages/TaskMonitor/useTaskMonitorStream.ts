// -*- coding: utf-8 -*-
/**
 * SSE stream hook for Task Monitor.
 *
 * Uses fetch + ReadableStream (not native EventSource) so that auth
 * headers are included automatically via buildAuthHeaders().
 * Auto-reconnects with exponential back-off.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getApiUrl } from "../../api/config";
import { buildAuthHeaders } from "../../api/authHeaders";
import type { SSEEvent } from "../../api/types/taskMonitor";

interface UseTaskMonitorStreamOptions {
  enabled?: boolean;
}

interface UseTaskMonitorStreamReturn {
  lastEvent: SSEEvent | null;
  connected: boolean;
}

const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000] as const;

export function useTaskMonitorStream(
  options: UseTaskMonitorStreamOptions = {},
): UseTaskMonitorStreamReturn {
  const { enabled = true } = options;
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const retryCountRef = useRef(0);
  const mountedRef = useRef(true);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(async () => {
    if (!enabled || !mountedRef.current) return;

    // Tear down any previous connection
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const url = getApiUrl("/task-monitor/stream");

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

        // Process complete SSE messages (delimited by \n\n)
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? ""; // Keep incomplete tail

        for (const part of parts) {
          const lines = part.split("\n");
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const payload = JSON.parse(line.slice(6)) as SSEEvent;
                if (mountedRef.current) {
                  setLastEvent(payload);
                }
              } catch {
                // Ignore malformed JSON
              }
            }
            // Ignore comments (": ping") and empty lines
          }
        }
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      if (err instanceof DOMException && err.name === "AbortError") return;
      // Connection lost or error — schedule reconnect
    } finally {
      if (mountedRef.current) {
        setConnected(false);
      }
    }

    // Schedule reconnect with back-off
    if (mountedRef.current && enabled) {
      const delay =
        BACKOFF_MS[Math.min(retryCountRef.current, BACKOFF_MS.length - 1)];
      retryCountRef.current += 1;
      retryTimerRef.current = setTimeout(() => {
        retryTimerRef.current = null;
        if (mountedRef.current && enabled) {
          connect();
        }
      }, delay);
    }
  }, [enabled]);

  useEffect(() => {
    mountedRef.current = true;
    if (enabled) {
      connect();
    }
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [connect, enabled]);

  return { lastEvent, connected };
}
