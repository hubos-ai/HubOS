# -*- coding: utf-8 -*-
"""S4c — real role source end-to-end.

Closes the gap between "TenantContext has a `roles` field" (Stage C
infra) and "the admin UI actually shows up when the owner logs in"
(S4). The role-source layer is implemented in
:func:`hubos.app.auth.resolve_user_roles` and consumed by
:func:`hubos.app.tenant_middleware.build_tenant_context`.

Branches under test
───────────────────

  1. Dev-mode host trust — HUBOS_AUTH_ENABLED unset → everyone admin.
  2. Auth enabled but no user registered yet (pre-install state) →
     still admin, so the first user can get to the register flow /
     dashboard without a role gate blocking them.
  3. Auth enabled + single user registered + that user authenticates →
     they are the owner and therefore admin.
  4. Auth enabled + single user registered + a *different* X-User-Id
     (via reverse proxy) → NOT admin (no role creep).
  5. HUBOS_ADMIN_USERS env-var allowlist → named principals get admin.
  6. Unauthenticated request cannot spoof admin via X-Roles header.
  7. Authenticated non-admin + X-Roles: "support" is merged in (proxy
     grants survive past auth) but does NOT grant admin unless the
     local source says so.
  8. End-to-end against the real admin router: branch (3) caller sees
     200 on /admin/sessions, branch (4) caller sees 403.

No subprocess. No full hubos app stack. We stand up a minimal FastAPI
with the real TenantContextMiddleware + a tiny shim that mimics
AuthMiddleware's `state.user` write, drive it with TestClient, and
point auth.py at a sandbox SECRET_DIR via monkeypatching.

Run: python3 scripts/test_s4c_role_source.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
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


# ---------------------------------------------------------------------------
# Sandbox: point auth.py at an isolated directory so the test can freely
# register/unregister users without touching the developer's real auth.json.
# We do this by monkey-patching the AUTH_FILE constant after import (the
# module reads it lazily on every call).
# ---------------------------------------------------------------------------

_TMP = Path(tempfile.mkdtemp(prefix="hubos_s4c_"))
_SANDBOX_AUTH = _TMP / "auth.json"
# Memory root must exist for the admin router (if we hit it) to not crash.
os.environ["HUBOS_MEMORY_ROOT"] = str(_TMP / "memory")
# Start with a clean env for every branch.
for k in ("HUBOS_AUTH_ENABLED", "HUBOS_ADMIN_USERS"):
    os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# Side-load the modules we need WITHOUT triggering `hubos.app.routers`
# which pulls agentscope + shortuuid + the whole skill tree. Same trick
# as scripts/test_admin_sessions_api.py.
# ---------------------------------------------------------------------------


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


for pkg_name, pkg_path in [
    ("hubos", SRC / "hubos"),
    ("hubos.app", SRC / "hubos" / "app"),
    ("hubos.app.routers", SRC / "hubos" / "app" / "routers"),
]:
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(pkg_path)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg


# A stub `hubos.constant` is needed because auth.py does `from ..constant
# import SECRET_DIR` and importing the real module would drag in the
# full hubos package. SECRET_DIR is redirected to our sandbox.
if "hubos.constant" not in sys.modules:
    stub = types.ModuleType("hubos.constant")
    stub.SECRET_DIR = _TMP  # type: ignore[attr-defined]
    stub.WORKING_DIR = _TMP  # type: ignore[attr-defined]
    sys.modules["hubos.constant"] = stub
    sys.modules["hubos"].constant = stub  # type: ignore[attr-defined]


auth_mod = _load_module(
    "hubos.app.auth",
    SRC / "hubos" / "app" / "auth.py",
)
# auth.py reads AUTH_FILE at module load; double-confirm it points into
# the sandbox. If SECRET_DIR stub above worked, this will already be
# correct.
assert str(auth_mod.AUTH_FILE).startswith(
    str(_TMP),
), f"AUTH_FILE escaped sandbox: {auth_mod.AUTH_FILE}"

tenant_mw_mod = _load_module(
    "hubos.app.tenant_middleware",
    SRC / "hubos" / "app" / "tenant_middleware.py",
)
admin_mod = _load_module(
    "hubos.app.routers.admin_sessions",
    SRC / "hubos" / "app" / "routers" / "admin_sessions.py",
)


from fastapi import FastAPI, Request  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


class _FakeAuthMiddleware(BaseHTTPMiddleware):
    """Mimics the write side of hubos.app.auth.AuthMiddleware.

    We explicitly do not exercise token verification here; every test
    drives the auth outcome by setting (or not setting) X-Test-User.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        user = request.headers.get("X-Test-User")
        request.state.user = user if user else None
        return await call_next(request)


def _build_app() -> FastAPI:
    app = FastAPI()
    # Registration order (Starlette applies reverse): outer=Auth, then
    # Tenant, then route.
    app.add_middleware(tenant_mw_mod.TenantContextMiddleware)
    app.add_middleware(_FakeAuthMiddleware)
    app.include_router(admin_mod.router, prefix="/api")
    return app


def _reset_env_and_users() -> None:
    os.environ.pop("HUBOS_AUTH_ENABLED", None)
    os.environ.pop("HUBOS_ADMIN_USERS", None)
    if _SANDBOX_AUTH.exists():
        _SANDBOX_AUTH.unlink()


def _register(username: str, password: str = "pw") -> None:
    token = auth_mod.register_user(username, password)
    assert token, f"failed to register {username}"


# Shorthand for purely-unit-level testing of resolve_user_roles.
resolve = auth_mod.resolve_user_roles


# ---------------------------------------------------------------------------
# T1. Dev-mode host trust.
# ---------------------------------------------------------------------------
print("\n[T1] dev mode → everyone admin")
_reset_env_and_users()
# HUBOS_AUTH_ENABLED not set, no users registered.
check(
    "resolve(None) grants admin",
    resolve(None) == frozenset({"admin"}),
    detail=str(resolve(None)),
)
check(
    "resolve('anyone') grants admin",
    resolve("anyone") == frozenset({"admin"}),
)
# Also verify via middleware + admin router round-trip.
admin_mod.reset_store_singleton_for_tests()
client = TestClient(_build_app())
r = client.get("/api/admin/sessions", headers={"X-Test-User": "anyone"})
check(
    "dev-mode request → admin router 200",
    r.status_code == 200,
    detail=f"{r.status_code} {r.text[:80]}",
)
# And without X-Test-User (anonymous). Since auth is disabled, skip-auth
# logic would let this through in production, so we also grant admin.
r = client.get("/api/admin/sessions")
check(
    "dev-mode anonymous also 200 (auth disabled)",
    r.status_code == 200,
)


# ---------------------------------------------------------------------------
# T2. Auth enabled, no users registered → pre-install state, still admin.
# ---------------------------------------------------------------------------
print("\n[T2] auth on, pre-install → admin")
_reset_env_and_users()
os.environ["HUBOS_AUTH_ENABLED"] = "true"
check(
    "resolve(None) admin when no users yet",
    resolve(None) == frozenset({"admin"}),
)
check(
    "resolve('alice') admin pre-install",
    resolve("alice") == frozenset({"admin"}),
)


# ---------------------------------------------------------------------------
# T3. Auth enabled + owner authenticates → admin.
# ---------------------------------------------------------------------------
print("\n[T3] auth on + owner authenticated → admin")
_reset_env_and_users()
os.environ["HUBOS_AUTH_ENABLED"] = "true"
_register("alice")
check(
    "owner alice → admin",
    resolve("alice") == frozenset({"admin"}),
)
check(
    "case-insensitive: resolve('ALICE') → admin",
    resolve("ALICE") == frozenset({"admin"}),
)
# Middleware round-trip.
admin_mod.reset_store_singleton_for_tests()
client = TestClient(_build_app())
r = client.get(
    "/api/admin/sessions",
    headers={"X-Test-User": "alice"},
)
check(
    "owner alice → admin router 200",
    r.status_code == 200,
    detail=f"{r.status_code}",
)


# ---------------------------------------------------------------------------
# T4. Non-owner principal (via X-User-Id proxy header) → NOT admin.
# ---------------------------------------------------------------------------
print("\n[T4] non-owner → NOT admin")
check(
    "stranger 'bob' gets empty role set",
    resolve("bob") == frozenset(),
    detail=str(resolve("bob")),
)
r = client.get(
    "/api/admin/sessions",
    headers={"X-Test-User": "bob"},
)
check(
    "non-owner 'bob' → admin router 403",
    r.status_code == 403,
    detail=f"{r.status_code}",
)
body = r.json()
check(
    "403 body has structured `forbidden` error",
    body.get("detail", {}).get("error") == "forbidden",
)


# ---------------------------------------------------------------------------
# T5. HUBOS_ADMIN_USERS allowlist.
# ---------------------------------------------------------------------------
print("\n[T5] HUBOS_ADMIN_USERS allowlist")
# alice is owner; add bob and Carol via allowlist.
os.environ["HUBOS_ADMIN_USERS"] = "bob, Carol"
check("bob via allowlist → admin", resolve("bob") == frozenset({"admin"}))
check(
    "Carol (case) via allowlist → admin",
    resolve("carol") == frozenset({"admin"}),
)
check("dave not listed → empty", resolve("dave") == frozenset())

# Middleware: bob now sees the admin page.
client = TestClient(_build_app())
r = client.get(
    "/api/admin/sessions",
    headers={"X-Test-User": "bob"},
)
check(
    "bob (allowlist) → 200",
    r.status_code == 200,
)
r = client.get(
    "/api/admin/sessions",
    headers={"X-Test-User": "dave"},
)
check(
    "dave (not listed) → 403",
    r.status_code == 403,
)
# Clear allowlist so later tests start from a known baseline.
os.environ.pop("HUBOS_ADMIN_USERS", None)


# ---------------------------------------------------------------------------
# T6. Unauthenticated + X-Roles: admin spoof is rejected (regression guard).
# ---------------------------------------------------------------------------
print("\n[T6] X-Roles spoof without auth is blocked")
# With auth enabled + a registered owner, an anonymous request claiming
# admin via X-Roles must not pass. This keeps us honest now that we
# merge X-Roles into the final set.
r = client.get(
    "/api/admin/sessions",
    headers={"X-Roles": "admin"},
)
check(
    "anonymous X-Roles=admin → 403",
    r.status_code == 403,
    detail=f"{r.status_code}",
)


# ---------------------------------------------------------------------------
# T7. Authenticated non-admin + X-Roles gives supplementary (non-admin)
#     roles but does NOT elevate to admin unless the local source says so.
#     Build the context directly so we can inspect the merged role set.
# ---------------------------------------------------------------------------
print("\n[T7] supplementary role merge")


# Fabricate a request-like object with the same duck typing as fastapi's
# Request exposes to build_tenant_context.
class _FakeReq:
    def __init__(self, user, headers):
        self.state = types.SimpleNamespace(user=user)
        self.headers = headers


ctx = tenant_mw_mod.build_tenant_context(
    _FakeReq("bob", {"X-Roles": "support, billing"}),
)
check(
    "authed non-admin merges supplementary roles",
    ctx.roles == frozenset({"support", "billing"}),
    detail=str(ctx.roles),
)

ctx = tenant_mw_mod.build_tenant_context(
    _FakeReq("alice", {"X-Roles": "support"}),
)
check(
    "authed owner keeps admin AND picks up supplementary",
    ctx.roles == frozenset({"admin", "support"}),
    detail=str(ctx.roles),
)

ctx = tenant_mw_mod.build_tenant_context(
    _FakeReq(None, {"X-Roles": "admin, support"}),
)
check(
    "anonymous drops all X-Roles claims (allowlist is empty)",
    ctx.roles == frozenset(),
    detail=str(ctx.roles),
)


# ---------------------------------------------------------------------------
# T8. resolve_user_roles treats blank / whitespace as unauthenticated.
# ---------------------------------------------------------------------------
print("\n[T8] edge cases")
check("resolve('') → empty", resolve("") == frozenset())
check("resolve('   ') → empty", resolve("   ") == frozenset())


# ---------------------------------------------------------------------------
# T9. Naming hygiene on the new/touched files.
# ---------------------------------------------------------------------------
print("\n[T9] naming hygiene")
forbidden = [chr(111) + "penclaw", "x" + "claw", "her" + "mes"]
touched = [
    SRC / "hubos/app/auth.py",
    SRC / "hubos/app/tenant_middleware.py",
]
for f in touched:
    blob = f.read_text(encoding="utf-8").lower()
    for bad in forbidden:
        check(f"{f.name} clean of {bad!r}", bad not in blob)


# ---------------------------------------------------------------------------
import shutil  # noqa: E402

shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 64)
print(f"Result: {'ALL PASSED' if FAIL == 0 else f'{FAIL} FAILED'}")
print("=" * 64)
sys.exit(0 if FAIL == 0 else 1)
