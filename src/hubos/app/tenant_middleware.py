# -*- coding: utf-8 -*-
"""FastAPI middleware that binds a :class:`TenantContext` per request.

Installed *after* :class:`~hubos.app.auth.AuthMiddleware` so that
``request.state.user`` (set by the auth layer) is already available.
The middleware translates that plus a few well-known headers into a
hubos.core :class:`~hubos.core.infra.tenant_context.TenantContext`, binds it
for the lifetime of the request, and tears it down on exit.

Header contract
---------------

Callers that sit behind a trusted reverse proxy (OpenWork, OAuth gateway,
etc.) can pass structured principal info via:

* ``X-User-Id``       — stable principal id. Falls back to
  ``request.state.user`` set by :class:`AuthMiddleware` if absent.
* ``X-Session-Id``    — GM session to scope memory against. Falls back
  to a header-derived default if absent (left to the router).
* ``X-Channel``       — originating channel (``"web"`` / ``"wechat"`` /
  …). Informational.
* ``X-Tenant-Id``     — workspace / org id for multi-tenant
  deployments. Optional.
* ``X-Roles``         — comma-separated role names. Untrusted requests
  (no authenticated user) get roles filtered to the safe baseline set.

Security posture
----------------

This middleware is deliberately conservative:

* If ``AuthMiddleware`` has not populated ``request.state.user`` AND the
  request is not on the auth-public list, the tenant context is bound
  empty (no user, no roles) — downstream handlers will then fail any
  :func:`require_roles` check.
* We never trust ``X-Roles`` from an unauthenticated request.
* The previously-bound context (if any) is always restored on exit,
  including on exception.

Wiring is deferred to the app startup step that owns middleware order;
see ``docs/architecture-state-machines.md`` §7 and the S4 admin-view
work item. This file is importable and unit-testable today without
being installed in the app yet.
"""
from __future__ import annotations

from typing import Iterable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from hubos.core.infra.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)

from .auth import resolve_user_roles


# Roles that may be granted to an unauthenticated request via ``X-Roles``.
# In practice we disallow *all* roles pre-auth; keep this as a frozenset
# so future relaxation stays explicit.
_ANON_ROLE_ALLOWLIST: frozenset[str] = frozenset()

# Header-derived role strings are split on comma and normalised — this
# matches what a typical proxy (nginx, envoy, OpenWork) emits.
_ROLE_SEP = ","


def _parse_roles(raw: str | None, *, authenticated: bool) -> frozenset[str]:
    if not raw:
        return frozenset()
    parts = [p.strip().lower() for p in raw.split(_ROLE_SEP)]
    filtered: Iterable[str] = (p for p in parts if p)
    if not authenticated:
        filtered = (p for p in filtered if p in _ANON_ROLE_ALLOWLIST)
    return frozenset(filtered)


def build_tenant_context(request: Request) -> TenantContext:
    """Pure function: turn a request into a :class:`TenantContext`.

    Roles come from two sources:

    * :func:`hubos.app.auth.resolve_user_roles` — the authoritative source
      based on the local user store (single-user ⇒ owner is admin, plus
      ``HUBOS_ADMIN_USERS`` allowlist, plus dev-mode host trust).
    * The ``X-Roles`` request header — only trusted when the request is
      authenticated (so an anonymous caller cannot spoof ``admin``). Used
      for proxy-injected supplementary grants in multi-user deployments.

    The final role set is the **union** of the two. Unauthenticated
    requests get an empty set.

    Exposed for tests and for handlers that need to re-build the context
    when spawning long-running tasks beyond the request lifetime.
    """
    headers = request.headers

    authed_user = getattr(request.state, "user", None)
    authenticated = authed_user is not None
    authed_username = authed_user if isinstance(authed_user, str) else None

    user_id = headers.get("X-User-Id") or authed_username
    session_id = headers.get("X-Session-Id")
    channel = headers.get("X-Channel")
    tenant_id = headers.get("X-Tenant-Id")

    header_roles = _parse_roles(
        headers.get("X-Roles"),
        authenticated=authenticated,
    )
    # Base roles come from the local user store. Dev mode (auth disabled
    # or no users registered) grants admin regardless of whether any
    # username is resolvable yet — matching AuthMiddleware's skip-auth
    # policy.
    base_roles = resolve_user_roles(authed_username)
    roles = header_roles | base_roles

    return TenantContext(
        user_id=user_id,
        session_id=session_id,
        channel=channel,
        roles=roles,
        tenant_id=tenant_id,
    )


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that binds a :class:`TenantContext` per request.

    Must be registered *after* :class:`~hubos.app.auth.AuthMiddleware` —
    Starlette applies middleware in reverse registration order, so in
    :mod:`hubos.app._app` that means calling ``add_middleware`` for this
    class *before* ``AuthMiddleware``.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        ctx = build_tenant_context(request)
        token = set_tenant_context(ctx)
        try:
            return await call_next(request)
        finally:
            reset_tenant_context(token)
