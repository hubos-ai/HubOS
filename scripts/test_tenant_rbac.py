"""Stage C (subset) — tenant_context + RBAC + middleware e2e.

Covers the three new pieces introduced on top of the sb-4 main line:

  hubos/core/infra/tenant_context.py   — async-safe ContextVar + dataclass
  hubos/core/infra/rbac.py             — require_roles / ensure_roles / has_*
  hubos/app/tenant_middleware.py     — FastAPI middleware that wires the two

The goal is a hard proof that:

  1. concurrent asyncio.Tasks carrying different principals NEVER leak
     into each other (this is the correctness property the whole admin
     API story will later depend on).
  2. require_roles gates sync and async callables consistently.
  3. TenantContextMiddleware binds / restores correctly even when the
     downstream handler raises.
  4. Unauthenticated requests cannot forge roles via X-Roles.

Run: python3 scripts/test_tenant_rbac.py
"""
from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))


FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAIL
    mark = "OK" if cond else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL += 1


# ======================================================================
# Import the new modules directly from hubos.core (no host deps needed).
# ======================================================================
from hubos.core.infra.tenant_context import (  # noqa: E402
    TenantContext,
    bind_tenant_context,
    current_channel,
    current_roles,
    current_session_id,
    current_tenant_id,
    current_user_id,
    get_tenant_context,
    reset_tenant_context,
    set_tenant_context,
)
from hubos.core.infra import rbac  # noqa: E402
from hubos.core.infra.rbac import (  # noqa: E402
    ForbiddenError,
    ensure_roles,
    has_all_roles,
    has_any_role,
    has_role,
    require_roles,
)


# ======================================================================
# T1. TenantContext dataclass shape.
# ======================================================================
print("\n[T1] TenantContext dataclass — immutability, merged(), as_mapping()")
ctx = TenantContext(
    user_id="u1",
    session_id="s1",
    channel="web",
    roles=frozenset({"user"}),
    tenant_id="t1",
)
# Frozen ⇒ attribute assignment must fail.
mutable_ok = True
try:
    ctx.user_id = "hacked"  # type: ignore[misc]
    mutable_ok = False
except Exception:
    pass
check("TenantContext is frozen", mutable_ok)

ctx2 = ctx.merged(roles=["user", "admin"], extra={"src": "test"})
check(
    "merged() normalises roles to frozenset",
    isinstance(ctx2.roles, frozenset) and ctx2.roles == frozenset({"user", "admin"}),
)
check(
    "merged() shallow-merges extra",
    ctx2.extra.get("src") == "test",
)
check(
    "merged() does NOT mutate original",
    ctx.roles == frozenset({"user"}) and ctx.extra == {},
)

mp = ctx2.as_mapping()
check(
    "as_mapping() exposes all fields + extra",
    mp["user_id"] == "u1"
    and mp["roles"] == ["admin", "user"]
    and mp.get("src") == "test",
    detail=str(mp),
)


# ======================================================================
# T2. ContextVar semantics — default empty + single-task set/reset.
# ======================================================================
print("\n[T2] ContextVar defaults + set/reset")
check(
    "default context is empty",
    get_tenant_context().user_id is None
    and current_roles() == frozenset(),
)

token = set_tenant_context(TenantContext(user_id="u2", roles=frozenset({"admin"})))
check("set_tenant_context binds", current_user_id() == "u2" and has_role("admin"))
reset_tenant_context(token)
check(
    "reset_tenant_context restores empty",
    current_user_id() is None and current_roles() == frozenset(),
)


# ======================================================================
# T3. bind_tenant_context context manager — exception-safe restore.
# ======================================================================
print("\n[T3] bind_tenant_context restores even on exception")
with bind_tenant_context(TenantContext(user_id="outer", roles=frozenset({"user"}))):
    check("outer bound", current_user_id() == "outer")

    try:
        with bind_tenant_context(
            TenantContext(user_id="inner", roles=frozenset({"admin"}))
        ):
            check("inner bound", current_user_id() == "inner" and has_role("admin"))
            raise RuntimeError("downstream boom")
    except RuntimeError:
        pass

    check(
        "after inner raised, outer is restored",
        current_user_id() == "outer" and not has_role("admin"),
    )

check(
    "after outer exits, context empty",
    current_user_id() is None,
)


# ======================================================================
# T4. Concurrent asyncio.Tasks do NOT leak context across each other.
#
# This is the core correctness property: 8 concurrent coroutines, each
# bound to its own user, perform many random await hops. At every hop
# the coroutine must still observe its OWN user_id.
# ======================================================================
print("\n[T4] concurrent tasks — per-principal isolation under await hops")


async def _worker(user_id: str, hops: int) -> tuple[str, bool]:
    with bind_tenant_context(
        TenantContext(
            user_id=user_id,
            session_id=f"s-{user_id}",
            channel="web",
            roles=frozenset({"user"}),
        )
    ):
        ok = True
        for _ in range(hops):
            await asyncio.sleep(random.uniform(0, 0.005))
            if current_user_id() != user_id:
                ok = False
                break
            if current_session_id() != f"s-{user_id}":
                ok = False
                break
        return user_id, ok


async def _parallel_isolation() -> list[tuple[str, bool]]:
    return await asyncio.gather(
        *[_worker(f"u{i}", hops=20) for i in range(8)]
    )


results = asyncio.run(_parallel_isolation())
check(
    "all 8 concurrent principals stayed isolated across 160 await hops",
    all(ok for _, ok in results),
    detail=str([(u, ok) for u, ok in results if not ok]),
)


# ======================================================================
# T5. require_roles — sync + async gating.
# ======================================================================
print("\n[T5] require_roles decorator — sync + async")


@require_roles("admin")
def sync_admin_only() -> str:
    return "ok"


@require_roles("admin")
async def async_admin_only() -> str:
    return "ok"


async def _exercise_decorator() -> None:
    with bind_tenant_context(
        TenantContext(user_id="u", roles=frozenset({"user"}))
    ):
        try:
            sync_admin_only()
            check("sync gate without role raises", False)
        except ForbiddenError as e:
            check(
                "sync gate without role raises ForbiddenError",
                e.required == ("admin",) and "admin" not in e.held,
            )
        try:
            await async_admin_only()
            check("async gate without role raises", False)
        except ForbiddenError:
            check("async gate without role raises ForbiddenError", True)

    with bind_tenant_context(
        TenantContext(user_id="a", roles=frozenset({"admin"}))
    ):
        check("sync gate with admin passes", sync_admin_only() == "ok")
        check("async gate with admin passes", await async_admin_only() == "ok")


asyncio.run(_exercise_decorator())


# ======================================================================
# T6. ensure_roles + any/all modes.
# ======================================================================
print("\n[T6] ensure_roles mode='any' / mode='all'")
with bind_tenant_context(
    TenantContext(user_id="u", roles=frozenset({"audit-reader"}))
):
    ok = True
    try:
        ensure_roles("admin", "audit-reader")  # mode=any default
    except ForbiddenError:
        ok = False
    check("mode=any satisfied when any role held", ok)

    ok = True
    try:
        ensure_roles("admin", "audit-reader", mode="all")
    except ForbiddenError:
        ok = False
    check(
        "mode=all rejects when only one of two held",
        not ok,
    )


# ======================================================================
# T7. has_role / has_any_role / has_all_roles — pure predicates.
# ======================================================================
print("\n[T7] has_role / has_any_role / has_all_roles")
with bind_tenant_context(
    TenantContext(user_id="u", roles=frozenset({"a", "b"}))
):
    check("has_role('a')", has_role("a"))
    check("not has_role('c')", not has_role("c"))
    check("has_any_role('c', 'b')", has_any_role("c", "b"))
    check("not has_any_role('x', 'y')", not has_any_role("x", "y"))
    check("has_all_roles('a', 'b')", has_all_roles("a", "b"))
    check("not has_all_roles('a', 'c')", not has_all_roles("a", "c"))
    check("has_any_role() with empty list trivially true", has_any_role())
    check("has_all_roles() with empty list trivially true", has_all_roles())


# ======================================================================
# T8. Bad mode rejected at call time.
# ======================================================================
print("\n[T8] invalid mode rejected")
bad_mode_ok = False
try:
    ensure_roles("admin", mode="either")  # type: ignore[arg-type]
except ValueError:
    bad_mode_ok = True
check("ensure_roles rejects mode='either'", bad_mode_ok)

bad_mode_ok = False
try:
    require_roles("admin", mode="xor")  # type: ignore[arg-type]
except ValueError:
    bad_mode_ok = True
check("require_roles rejects mode='xor'", bad_mode_ok)


# ======================================================================
# T9. Generator functions rejected by require_roles (explicit TypeError).
# ======================================================================
print("\n[T9] require_roles refuses generator functions")
gen_ok = False
try:
    @require_roles("admin")
    def _gen():
        yield 1
except TypeError:
    gen_ok = True
check("decorating a generator raises TypeError", gen_ok)


# ======================================================================
# T10. TenantContextMiddleware — build_tenant_context purity + gating.
# ======================================================================
print("\n[T10] build_tenant_context — purely derives ctx from request")

# We import the middleware lazily since FastAPI may not be installed in
# minimal envs. Fall back to a skip if so.
try:
    from hubos.app import tenant_middleware as _tm_mod  # noqa: E402
    from hubos.app.tenant_middleware import (  # noqa: E402
        TenantContextMiddleware,
        build_tenant_context,
    )
    # S4c added a second role source (hubos.app.auth.resolve_user_roles)
    # that the middleware merges into the final role set. These tests
    # target the X-Roles gating logic in isolation, so we stub the
    # role-source layer to an empty frozenset. The role source itself is
    # exercised end-to-end in scripts/test_s4c_role_source.py.
    _tm_mod.resolve_user_roles = lambda _u: frozenset()  # type: ignore[assignment]
    _MW_OK = True
except Exception as e:  # noqa: BLE001
    print(f"  [SKIP] fastapi not importable: {e!r}")
    _MW_OK = False

if _MW_OK:
    class _FakeState:
        def __init__(self, user=None):
            self.user = user

    class _FakeReq:
        def __init__(self, headers: dict[str, str], user=None):
            self.headers = headers
            self.state = _FakeState(user=user)

    # T10a: authenticated request with full headers
    req = _FakeReq(
        headers={
            "X-User-Id": "alice",
            "X-Session-Id": "sess-1",
            "X-Channel": "web",
            "X-Tenant-Id": "acme",
            "X-Roles": "admin, session-reader",
        },
        user="alice",
    )
    c = build_tenant_context(req)  # type: ignore[arg-type]
    check(
        "authed request → full ctx",
        c.user_id == "alice"
        and c.session_id == "sess-1"
        and c.channel == "web"
        and c.tenant_id == "acme"
        and c.roles == frozenset({"admin", "session-reader"}),
        detail=str(c),
    )

    # T10b: unauthenticated request cannot forge roles.
    req_anon = _FakeReq(
        headers={
            "X-User-Id": "evilcorp",
            "X-Roles": "admin",
        },
        user=None,
    )
    c2 = build_tenant_context(req_anon)  # type: ignore[arg-type]
    check(
        "unauthenticated request cannot forge admin role",
        c2.roles == frozenset() and c2.user_id == "evilcorp",
        detail=str(c2),
    )

    # T10c: fallback — no X-User-Id header but state.user set
    req_f = _FakeReq(headers={}, user="bob")
    c3 = build_tenant_context(req_f)  # type: ignore[arg-type]
    check(
        "falls back to request.state.user when X-User-Id absent",
        c3.user_id == "bob" and c3.roles == frozenset(),
    )

    # T10d: middleware binds + restores even on exception
    async def _exercise_middleware():
        mw = TenantContextMiddleware(app=None)  # type: ignore[arg-type]

        # Baseline: before any request, context is empty.
        before = get_tenant_context().user_id
        observed = {}

        async def _boom_handler(_req):
            observed["inside_user"] = current_user_id()
            observed["inside_roles"] = set(current_roles())
            raise RuntimeError("handler exploded")

        req_ok = _FakeReq(
            headers={"X-User-Id": "carol", "X-Roles": "admin"},
            user="carol",
        )
        # Call dispatch directly with our fake req + failing handler.
        raised = False
        try:
            await mw.dispatch(req_ok, _boom_handler)  # type: ignore[arg-type]
        except RuntimeError:
            raised = True

        check("handler exception propagates", raised)
        check(
            "context WAS bound inside handler",
            observed.get("inside_user") == "carol"
            and observed.get("inside_roles") == {"admin"},
        )
        after = get_tenant_context().user_id
        check(
            "context restored after exception",
            before is None and after is None,
        )

    asyncio.run(_exercise_middleware())


# ======================================================================
# T11. Naming hygiene on the new source files.
# ======================================================================
print("\n[T11] naming hygiene on new files")
forbidden = [chr(111) + "penclaw", "x" + "claw", "her" + "mes"]
new_files = [
    SRC / "hubos/core/infra/tenant_context.py",
    SRC / "hubos/core/infra/rbac.py",
    SRC / "hubos/app/tenant_middleware.py",
]
for f in new_files:
    blob = f.read_text(encoding="utf-8").lower()
    for bad in forbidden:
        check(f"{f.name} clean of {bad!r}", bad not in blob)


# ======================================================================
print("\n" + "=" * 64)
print(f"Result: {'ALL PASSED' if FAIL == 0 else f'{FAIL} FAILED'}")
print("=" * 64)
sys.exit(0 if FAIL == 0 else 1)
