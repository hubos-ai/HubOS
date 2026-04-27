# -*- coding: utf-8 -*-
"""Async-safe tenant / user / session context.

This module provides the canonical place for per-request ambient state
(user_id, session_id, channel, roles, tenant_id). It is designed to be:

* **Async-safe**: backed by :class:`contextvars.ContextVar`, so each
  ``asyncio.Task`` (and each thread) sees its own value. Concurrent
  requests handled by the same process cannot read each other's context.

* **Host-agnostic**: lives inside ``hubos.core`` and imports nothing from
  the host application. The host wires the context by calling
  :func:`set_tenant_context` / :func:`bind_tenant_context` at the entry
  point it owns (FastAPI middleware, CLI dispatcher, worker framework,
  etc.).

* **Immutable values**: :class:`TenantContext` is a frozen dataclass. To
  change a field, build a new context with :meth:`TenantContext.merged`
  and re-bind it. This keeps reasoning local — a reference captured at
  one point can never be mutated later from another coroutine.

The context is intentionally *not* hierarchical. If a nested call needs a
different principal (sub-agent delegation, system-initiated job), use
:func:`bind_tenant_context` as a context manager to push a new context
for the nested frame and automatically restore the outer one on exit.

Example::

    from hubos.core.infra.tenant_context import (
        TenantContext, bind_tenant_context, current_user_id,
    )

    async def handle_request(req):
        ctx = TenantContext(
            user_id=req.headers["X-User-Id"],
            session_id=req.headers["X-Session-Id"],
            channel="web",
            roles=frozenset({"user"}),
        )
        with bind_tenant_context(ctx):
            # everything in this ``with`` block (including spawned tasks
            # that copy the current context) sees the bound context
            await downstream_work()

    async def downstream_work():
        uid = current_user_id()
        ...
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from typing import FrozenSet, Iterator, Mapping, Optional


@dataclass(frozen=True)
class TenantContext:
    """Snapshot of the current request's principal + scope.

    All fields are optional individually; an empty ``TenantContext()`` is
    valid and represents "no principal bound" (e.g. a system task). Code
    that requires a principal should check explicitly, not assume.

    Attributes:
        user_id: stable identifier for the human principal. In the
            single-user mode of the host app this will be the username;
            in multi-tenant deployments it will typically be a uuid.
        session_id: current GM conversation session. Scopes short-term
            and mid-term memory (L2 / L3); see
            ``docs/architecture-memory-layers.md``.
        channel: entry channel (``"web"``, ``"wechat"``, ``"cli"``...).
            Informational; not used for authorisation.
        roles: frozenset of role names the principal holds. Convention:
            lowercase, hyphen-separated ASCII, e.g. ``"admin"``,
            ``"session-reader"``. Empty set ⇒ baseline access only.
        tenant_id: organisation / workspace grouping, for future multi-
            tenant deployments. Optional; leave ``None`` for single-
            tenant setups.
        extra: opaque string→string map for niche forwarded headers. Do
            NOT use this to carry authorisation-relevant data — add a
            first-class field instead.
    """

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    channel: Optional[str] = None
    roles: FrozenSet[str] = field(default_factory=frozenset)
    tenant_id: Optional[str] = None
    extra: Mapping[str, str] = field(default_factory=dict)

    def merged(self, **overrides: object) -> "TenantContext":
        """Return a new context with ``overrides`` applied.

        ``roles`` accepts any iterable and is normalised to a frozenset.
        ``extra`` is shallow-merged rather than replaced.
        """
        normalised: dict[str, object] = {}
        for k, v in overrides.items():
            if k == "roles" and v is not None:
                normalised[k] = frozenset(v)  # type: ignore[arg-type]
            elif k == "extra" and v is not None:
                combined = dict(self.extra)
                combined.update(v)  # type: ignore[arg-type]
                normalised[k] = combined
            else:
                normalised[k] = v
        return replace(self, **normalised)  # type: ignore[arg-type]

    def as_mapping(self) -> dict[str, object]:
        """Render as a plain dict, suitable for logging or bridging to
        pre-existing string-keyed context systems."""
        out: dict[str, object] = {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "channel": self.channel,
            "tenant_id": self.tenant_id,
            "roles": sorted(self.roles),
        }
        out.update(self.extra)
        return out


# Sentinel default keeps ``get_tenant_context()`` cheap (no allocation
# unless the caller never bound one).
_EMPTY = TenantContext()

_ctx_var: ContextVar[TenantContext] = ContextVar(
    "hubos_tenant_ctx",
    default=_EMPTY,
)


def get_tenant_context() -> TenantContext:
    """Return the context bound to the current task, or an empty one."""
    return _ctx_var.get()


def set_tenant_context(ctx: Optional[TenantContext]) -> Token:
    """Bind ``ctx`` unconditionally and return a reset :class:`Token`.

    Prefer :func:`bind_tenant_context` for scoped binding. Use this only
    when the caller owns the whole lifecycle (e.g. in a middleware that
    resets explicitly in a ``finally``).
    """
    return _ctx_var.set(ctx if ctx is not None else _EMPTY)


def reset_tenant_context(token: Token) -> None:
    """Undo the most recent :func:`set_tenant_context` call."""
    _ctx_var.reset(token)


@contextmanager
def bind_tenant_context(
    ctx: Optional[TenantContext],
) -> Iterator[TenantContext]:
    """Push ``ctx`` for the duration of the ``with`` block.

    Yields the bound context so callers can ``with bind_tenant_context(c) as c:``
    conveniently.
    """
    bound = ctx if ctx is not None else _EMPTY
    token = _ctx_var.set(bound)
    try:
        yield bound
    finally:
        _ctx_var.reset(token)


# Convenience accessors — the hot path for tools / services that only
# need one field. They do not allocate when no context is bound.


def current_user_id() -> Optional[str]:
    return _ctx_var.get().user_id


def current_session_id() -> Optional[str]:
    return _ctx_var.get().session_id


def current_channel() -> Optional[str]:
    return _ctx_var.get().channel


def current_roles() -> FrozenSet[str]:
    return _ctx_var.get().roles


def current_tenant_id() -> Optional[str]:
    return _ctx_var.get().tenant_id
