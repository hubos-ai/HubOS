/**
 * useIsAdmin — probe-based role detection.
 *
 * Since the console has no dedicated "/auth/me" endpoint and the backend
 * derives roles from request headers bound by `TenantContextMiddleware`,
 * we determine whether the current caller is an admin by doing a cheap
 * HEAD-equivalent GET against the admin surface itself (`?limit=1`).
 *
 * Semantics
 * ─────────
 *   • 200      → admin, show the menu entry and route.
 *   • 403      → authenticated but not admin, hide silently.
 *   • 401      → request.ts already redirects to /login, so we end up
 *                in a "loading / anonymous" state the hook shouldn't
 *                crash on.
 *   • other    → transient / network / server error — treat as non-admin
 *                for safety, but expose the raw kind so the page that
 *                gets navigated into directly can show a clear error
 *                banner instead of an empty state.
 *
 * The result is cached per module import (process-wide, cleared on
 * `clearAdminProbe()` after logout) so the sidebar doesn't re-probe on
 * every navigation.
 */

import { useCallback, useEffect, useState } from "react";

import {
  adminSessionsApi,
  classifyError,
  type AdminSessionsErrorKind,
} from "../api/modules/adminSessions";

type ProbeStatus = "loading" | "admin" | "denied" | "error";

interface ProbeResult {
  status: ProbeStatus;
  errorKind: AdminSessionsErrorKind | null;
}

let cached: ProbeResult | null = null;
let inflight: Promise<ProbeResult> | null = null;

async function runProbe(): Promise<ProbeResult> {
  try {
    await adminSessionsApi.probe();
    return { status: "admin", errorKind: null };
  } catch (err) {
    const e = classifyError(err);
    if (e.kind === "forbidden") {
      return { status: "denied", errorKind: "forbidden" };
    }
    if (e.kind === "unauthorized") {
      // request.ts already redirected us. Treat as loading so the UI
      // doesn't flash a denied banner on the way out.
      return { status: "loading", errorKind: "unauthorized" };
    }
    return { status: "error", errorKind: e.kind };
  }
}

export function clearAdminProbe() {
  cached = null;
  inflight = null;
}

export interface UseIsAdminResult {
  status: ProbeStatus;
  isAdmin: boolean;
  errorKind: AdminSessionsErrorKind | null;
  refetch: () => void;
}

export function useIsAdmin(): UseIsAdminResult {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<ProbeResult>(
    cached ?? { status: "loading", errorKind: null },
  );

  useEffect(() => {
    let cancelled = false;
    if (cached && tick === 0) {
      setState(cached);
      return;
    }
    if (!inflight) {
      inflight = runProbe().then((r) => {
        cached = r;
        inflight = null;
        return r;
      });
    }
    inflight.then((r) => {
      if (!cancelled) setState(r);
    });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  // Stabilize `refetch` so downstream useEffect/useCallback consumers don't
  // treat every render as a dependency change and re-run their effects.
  const refetch = useCallback(() => {
    cached = null;
    inflight = null;
    setTick((t) => t + 1);
  }, []);

  return {
    status: state.status,
    isAdmin: state.status === "admin",
    errorKind: state.errorKind,
    refetch,
  };
}
