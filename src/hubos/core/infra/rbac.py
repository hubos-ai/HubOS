# -*- coding: utf-8 -*-
"""Role-based access control (RBAC) primitives.

Role checks resolve against the current :class:`~.tenant_context.TenantContext`.
This module provides three complementary surfaces:

* :func:`has_role`, :func:`has_any_role`, :func:`has_all_roles` — pure
  predicate checks. Cheap, never raise, suitable for ``if`` branches in
  UI-facing logic that wants to degrade gracefully.

* :func:`ensure_roles` — imperative guard that raises
  :class:`ForbiddenError` when the current principal does not satisfy
  the requirement. Use at the start of server-side handlers / tool
  implementations where the correct reaction to missing roles is to
  abort.

* :func:`require_roles` — decorator form of :func:`ensure_roles` that
  wraps sync AND async callables transparently. Use for short protected
  functions where a decorator reads better than an inline guard.

All three honour a ``mode`` parameter:

* ``"any"`` (default) — principal satisfies the check if it holds *at
  least one* of the listed roles. Most common for typical "admin OR
  session-reader" patterns.
* ``"all"`` — principal must hold *every* listed role. Use for
  compound privileges like "can read audit logs" = ``{"admin",
  "audit-reader"}``.

There is intentionally no wildcard / super-user role baked in. If you
want a super-user, grant it every role explicitly; this keeps audit
trails honest.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any, Awaitable, Callable, Iterable, TypeVar

from .tenant_context import current_roles, current_user_id

F = TypeVar("F", bound=Callable[..., Any])


class ForbiddenError(PermissionError):
    """Raised when the current principal fails an RBAC check.

    Carries enough structured detail for middleware to render a useful
    HTTP 403 response without having to parse the exception message.
    """

    def __init__(
        self,
        *,
        required: Iterable[str],
        mode: str,
        held: Iterable[str],
        user_id: str | None,
    ) -> None:
        self.required: tuple[str, ...] = tuple(required)
        self.mode = mode
        self.held: tuple[str, ...] = tuple(sorted(held))
        self.user_id = user_id
        msg = (
            f"Principal {user_id!r} lacks required role(s) "
            f"{list(self.required)} (mode={mode}); held={list(self.held)}."
        )
        super().__init__(msg)


def _normalise_mode(mode: str) -> str:
    if mode not in ("any", "all"):
        raise ValueError(f"mode must be 'any' or 'all', not {mode!r}")
    return mode


def _satisfied(required: tuple[str, ...], mode: str) -> bool:
    held = current_roles()
    if not required:
        return True
    if mode == "any":
        return any(r in held for r in required)
    return all(r in held for r in required)


# ─── Predicates (never raise) ──────────────────────────────────────────


def has_role(role: str) -> bool:
    """True iff the current principal holds ``role``."""
    return role in current_roles()


def has_any_role(*roles: str) -> bool:
    """True iff the current principal holds at least one of ``roles``."""
    if not roles:
        return True
    held = current_roles()
    return any(r in held for r in roles)


def has_all_roles(*roles: str) -> bool:
    """True iff the current principal holds every one of ``roles``."""
    if not roles:
        return True
    held = current_roles()
    return all(r in held for r in roles)


# ─── Imperative guard (raises ForbiddenError) ──────────────────────────


def ensure_roles(*roles: str, mode: str = "any") -> None:
    """Raise :class:`ForbiddenError` if the current principal fails the check.

    No-op when ``roles`` is empty.
    """
    mode = _normalise_mode(mode)
    required = tuple(roles)
    if _satisfied(required, mode):
        return
    raise ForbiddenError(
        required=required,
        mode=mode,
        held=current_roles(),
        user_id=current_user_id(),
    )


# ─── Decorator form ────────────────────────────────────────────────────


def require_roles(*roles: str, mode: str = "any") -> Callable[[F], F]:
    """Decorator that enforces :func:`ensure_roles` before the wrapped call.

    Supports both sync and async callables. For async callables, the
    check runs *before* awaiting, so the coroutine body never starts if
    the principal is forbidden — important for avoiding partial side
    effects. Generator / async-generator functions are not supported;
    wrap them in a coroutine if you need gating.

    Examples::

        @require_roles("admin")
        async def list_all_sessions() -> list[str]: ...

        @require_roles("admin", "audit-reader", mode="all")
        def export_audit_blob() -> bytes: ...
    """
    mode = _normalise_mode(mode)
    required = tuple(roles)

    def decorate(fn: F) -> F:
        if inspect.isasyncgenfunction(fn) or inspect.isgeneratorfunction(fn):
            raise TypeError(
                "require_roles does not support generator functions; "
                "wrap the generator in a coroutine.",
            )

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                if not _satisfied(required, mode):
                    raise ForbiddenError(
                        required=required,
                        mode=mode,
                        held=current_roles(),
                        user_id=current_user_id(),
                    )
                return await fn(*args, **kwargs)

            return awrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def swrapper(*args: Any, **kwargs: Any) -> Any:
            if not _satisfied(required, mode):
                raise ForbiddenError(
                    required=required,
                    mode=mode,
                    held=current_roles(),
                    user_id=current_user_id(),
                )
            return fn(*args, **kwargs)

        return swrapper  # type: ignore[return-value]

    return decorate


# ─── Awaitable bridge ──────────────────────────────────────────────────


def gate(
    awaitable: Awaitable[Any],
    *roles: str,
    mode: str = "any",
) -> Awaitable[Any]:
    """Return an awaitable that raises before starting if the check fails.

    Handy when the coroutine is produced externally (e.g. a worker
    dispatched a raw ``asyncio.Task`` before the check ran)::

        await rbac.gate(worker.execute(req), "admin")
    """
    mode = _normalise_mode(mode)
    required = tuple(roles)

    async def _run() -> Any:
        if not _satisfied(required, mode):
            raise ForbiddenError(
                required=required,
                mode=mode,
                held=current_roles(),
                user_id=current_user_id(),
            )
        return await awaitable

    return _run()
