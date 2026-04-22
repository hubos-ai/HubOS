"""S4a — admin sessions API e2e.

Spins up a minimal FastAPI app that mounts:

  • TenantContextMiddleware            (real)
  • the admin_sessions router          (real)
  • a fake "auth-installed" middleware that sets request.state.user +
    forwards X-Roles through to TenantContextMiddleware exactly like
    the real AuthMiddleware would.

Then drives it with starlette.testclient.TestClient (real HTTP round
trip, in process) to verify:

  1. 403 for anonymous / missing role
  2. 200 for admin role
  3. cross-user listing (admin sees sessions belonging to different users)
  4. filtering (q, user_id)
  5. pagination (limit / offset)
  6. 404 for unknown session id
  7. message-window endpoint (offset + limit)
  8. large-session tail truncation on detail endpoint
  9. naming hygiene

Run: python3 scripts/test_admin_sessions_api.py
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))


# Memory/workspace sandboxes MUST be set before hubos modules are imported.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="hubos_admin_api_"))
_TMP_WORKING_DIR = _TMP_ROOT / "working"
_TMP_WORKING_DIR.mkdir(parents=True, exist_ok=True)
os.environ["HUBOS_MEMORY_ROOT"] = str(_TMP_ROOT)
os.environ["HUBOS_WORKING_DIR"] = str(_TMP_WORKING_DIR)
os.environ["HUBOS_SKIP_LEGACY_MIGRATION"] = "1"
atexit.register(lambda: shutil.rmtree(_TMP_ROOT, ignore_errors=True))


FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAIL
    mark = "OK" if cond else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL += 1


# ---------------------------------------------------------------------------
# Build a minimal test app. We do NOT import hubos.app._app (it drags in
# agentscope, the whole skill system, etc.). We just compose the two
# pieces that matter for this API surface.
# ---------------------------------------------------------------------------
from fastapi import FastAPI, Request  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

# Admin router and the tenant middleware each have minimal, clean
# dependency footprints; but sibling router modules in hubos.app.routers
# (e.g. agent.py) transitively pull agentscope + shortuuid, which are
# heavy/optional in a hermetic test environment. We therefore side-load
# each target module from its source path, bypassing the package __init__.
import importlib.util  # noqa: E402
import types  # noqa: E402


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Provide lightweight namespace packages so relative imports inside the
# loaded modules resolve. Both admin_sessions.py (routers package) and
# tenant_middleware.py (hubos.app package) only use absolute imports into
# hubos.core, so no stubbing of hubos internals is actually required, but
# we still have to register the parents so "from hubos.app..." works.
for pkg_name, pkg_path in [
    ("hubos", SRC / "hubos"),
    ("hubos.app", SRC / "hubos" / "app"),
    ("hubos.app.routers", SRC / "hubos" / "app" / "routers"),
]:
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(pkg_path)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg

# S4c: tenant_middleware now imports `resolve_user_roles` from
# hubos.app.auth. Stub it out so this test stays focused on X-Roles
# gating + admin router mechanics. The role-source layer has its own
# dedicated test in scripts/test_s4c_role_source.py.
_auth_stub = types.ModuleType("hubos.app.auth")
_auth_stub.resolve_user_roles = lambda _u: frozenset()  # type: ignore[attr-defined]
sys.modules["hubos.app.auth"] = _auth_stub

_tenant_mw_mod = _load_module(
    "hubos.app.tenant_middleware",
    SRC / "hubos" / "app" / "tenant_middleware.py",
)
TenantContextMiddleware = _tenant_mw_mod.TenantContextMiddleware

_admin_mod = _load_module(
    "hubos.app.routers.admin_sessions",
    SRC / "hubos" / "app" / "routers" / "admin_sessions.py",
)
admin_router = _admin_mod.router
reset_store_singleton_for_tests = _admin_mod.reset_store_singleton_for_tests

from hubos.core.memory.local_store import LocalMemoryStore  # noqa: E402


class _FakeAuthMiddleware(BaseHTTPMiddleware):
    """Stand-in for hubos.app.auth.AuthMiddleware.

    The real middleware parses Bearer tokens and sets
    ``request.state.user``; here we trust a test-only ``X-Test-User``
    header to keep the test hermetic. We never let this middleware run
    in production — it is defined inline in the test file.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        user = request.headers.get("X-Test-User")
        request.state.user = user if user else None
        return await call_next(request)


def _build_app() -> FastAPI:
    app = FastAPI()
    # Registration order matters (Starlette applies middleware in REVERSE
    # of registration). Effective runtime order must be:
    #     Auth → Tenant → route
    # i.e. Auth runs first so state.user is populated before Tenant reads it.
    app.add_middleware(TenantContextMiddleware)  # inner
    app.add_middleware(_FakeAuthMiddleware)      # outer
    app.include_router(admin_router, prefix="/api")
    return app


# ---------------------------------------------------------------------------
# Seed L4: three sessions, two users.
# ---------------------------------------------------------------------------
def _seed_store() -> None:
    reset_store_singleton_for_tests()
    store = LocalMemoryStore()
    now = datetime.now(timezone.utc)

    def _iso(delta_minutes: int) -> str:
        return (now - timedelta(minutes=delta_minutes)).isoformat()

    store.create_session(
        "sess-a1",
        {
            "session_id": "sess-a1",
            "title": "Quarterly review deck",
            "started": _iso(30),
            "agent_id": "finance",
            "channel": "web",
            "user_id": "alice",
            "tags": ["deck", "Q4"],
        },
    )
    for i in range(3):
        store.append_message(
            "sess-a1",
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"a1-msg-{i}",
                "timestamp": _iso(29 - i),
            },
        )

    store.create_session(
        "sess-a2",
        {
            "session_id": "sess-a2",
            "title": "Travel plan — Berlin",
            "started": _iso(20),
            "agent_id": "general",
            "channel": "web",
            "user_id": "alice",
            "tags": ["travel"],
        },
    )
    store.append_message(
        "sess-a2",
        {"role": "user", "content": "book hotel", "timestamp": _iso(19)},
    )

    store.create_session(
        "sess-b1",
        {
            "session_id": "sess-b1",
            "title": "Quarterly review code walkthrough",
            "started": _iso(10),
            "agent_id": "engineering",
            "channel": "wechat",
            "user_id": "bob",
            "tags": ["engineering", "Q4"],
        },
    )
    for i in range(250):
        store.append_message(
            "sess-b1",
            {"role": "user", "content": f"big-{i}", "timestamp": _iso(5)},
        )


_seed_store()
app = _build_app()
client = TestClient(app)


# ---------------------------------------------------------------------------
# T1. 403 when no admin role.
# ---------------------------------------------------------------------------
print("\n[T1] 403 without admin role")

r = client.get("/api/admin/sessions")
check("anonymous → 403", r.status_code == 403, detail=f"{r.status_code} {r.text[:100]}")

r = client.get(
    "/api/admin/sessions",
    headers={"X-Test-User": "alice", "X-User-Id": "alice"},
)
check(
    "authenticated but no role → 403",
    r.status_code == 403,
    detail=f"{r.status_code}",
)

r = client.get(
    "/api/admin/sessions",
    headers={"X-Test-User": "alice", "X-User-Id": "alice", "X-Roles": "user"},
)
check(
    "authenticated with 'user' role (not admin) → 403",
    r.status_code == 403,
    detail=f"{r.status_code}",
)

body = r.json()
check(
    "403 body has structured error",
    body.get("detail", {}).get("error") == "forbidden"
    and body["detail"].get("required_roles") == ["admin"],
    detail=str(body)[:150],
)


# ---------------------------------------------------------------------------
# T2. Anonymous request cannot forge admin via X-Roles.
#     (TenantContextMiddleware filters X-Roles when state.user is None.)
# ---------------------------------------------------------------------------
print("\n[T2] unauthenticated X-Roles=admin spoof rejected")

r = client.get("/api/admin/sessions", headers={"X-Roles": "admin"})
check(
    "no X-Test-User + X-Roles=admin → 403",
    r.status_code == 403,
    detail=f"{r.status_code}",
)


# ---------------------------------------------------------------------------
# T3. admin role → 200 and sees ALL 3 sessions cross-user.
# ---------------------------------------------------------------------------
print("\n[T3] admin lists all sessions across users")

admin_hdr = {
    "X-Test-User": "root",
    "X-User-Id": "root",
    "X-Roles": "admin",
}
r = client.get("/api/admin/sessions", headers=admin_hdr)
check("admin → 200", r.status_code == 200, detail=str(r.status_code))
body = r.json()
check("response has total", isinstance(body.get("total"), int))
check(
    "admin sees all 3 seeded sessions",
    body.get("total") == 3,
    detail=f"total={body.get('total')}",
)
ids = {s["session_id"] for s in body["sessions"]}
check("ids are {a1,a2,b1}", ids == {"sess-a1", "sess-a2", "sess-b1"},
      detail=str(ids))
# Most-recent first: sess-b1 started at -10 min, sess-a2 at -20, sess-a1 at -30.
first_id = body["sessions"][0]["session_id"]
check(
    "sessions ordered most-recent-first",
    first_id == "sess-b1",
    detail=first_id,
)


# ---------------------------------------------------------------------------
# T4. query + user_id filters.
# ---------------------------------------------------------------------------
print("\n[T4] q + user_id filtering")

r = client.get("/api/admin/sessions?q=Quarterly", headers=admin_hdr)
body = r.json()
check(
    "q=Quarterly matches 2 sessions",
    body["total"] == 2,
    detail=f"got {body['total']}",
)

r = client.get("/api/admin/sessions?user_id=alice", headers=admin_hdr)
body = r.json()
check(
    "user_id=alice matches alice's 2 sessions",
    body["total"] == 2
    and all(s["user_id"] == "alice" for s in body["sessions"]),
)

r = client.get("/api/admin/sessions?user_id=nobody", headers=admin_hdr)
body = r.json()
check("unknown user_id → empty", body["total"] == 0)

r = client.get("/api/admin/sessions?channel=wechat", headers=admin_hdr)
body = r.json()
check(
    "channel=wechat returns only sess-b1",
    body["total"] == 1 and body["sessions"][0]["session_id"] == "sess-b1",
)


# ---------------------------------------------------------------------------
# T5. limit / offset pagination.
# ---------------------------------------------------------------------------
print("\n[T5] pagination")

r = client.get("/api/admin/sessions?limit=2&offset=0", headers=admin_hdr)
body = r.json()
check(
    "page0: limit=2 returns 2 rows of 3 total",
    body["total"] == 3
    and len(body["sessions"]) == 2
    and body["limit"] == 2
    and body["offset"] == 0,
)

r = client.get("/api/admin/sessions?limit=2&offset=2", headers=admin_hdr)
body = r.json()
check(
    "page1: offset=2 returns the remaining 1 row",
    body["total"] == 3 and len(body["sessions"]) == 1,
)


# ---------------------------------------------------------------------------
# T6. Detail endpoint — found, 404, owner info visible to admin.
# ---------------------------------------------------------------------------
print("\n[T6] GET /admin/sessions/{id}")

r = client.get("/api/admin/sessions/sess-a1", headers=admin_hdr)
check("sess-a1 → 200", r.status_code == 200)
body = r.json()
check(
    "admin sees alice's session even though caller is root",
    body["metadata"]["user_id"] == "alice"
    and body["metadata"]["title"] == "Quarterly review deck",
)
check(
    "messages returned (3)",
    body["total_messages"] == 3 and len(body["messages"]) == 3,
)
check("truncated=False for small session", body["truncated"] is False)

r = client.get("/api/admin/sessions/does-not-exist", headers=admin_hdr)
check("unknown id → 404", r.status_code == 404)
check(
    "404 body has structured error",
    r.json().get("detail", {}).get("error") == "not_found",
)


# ---------------------------------------------------------------------------
# T7. Detail endpoint — last_n truncation on big session.
# ---------------------------------------------------------------------------
print("\n[T7] last_n truncation")

r = client.get(
    "/api/admin/sessions/sess-b1?last_n=50",
    headers=admin_hdr,
)
body = r.json()
check("truncated=True on 250-msg session", body["truncated"] is True)
check("returned tail size == 50", len(body["messages"]) == 50)
check("total_messages reports full 250", body["total_messages"] == 250)
# Tail must be the LAST 50 messages, i.e. content ends with 'big-249'.
check(
    "tail is the most recent messages (last message is 'big-249')",
    body["messages"][-1]["content"] == "big-249",
)


# ---------------------------------------------------------------------------
# T8. /messages window endpoint.
# ---------------------------------------------------------------------------
print("\n[T8] GET /admin/sessions/{id}/messages window")

r = client.get(
    "/api/admin/sessions/sess-b1/messages?offset=100&limit=25",
    headers=admin_hdr,
)
check("messages window → 200", r.status_code == 200)
body = r.json()
check("total=250", body["total"] == 250)
check(
    "window is messages 100..124",
    len(body["messages"]) == 25
    and body["messages"][0]["content"] == "big-100"
    and body["messages"][-1]["content"] == "big-124",
)

r = client.get(
    "/api/admin/sessions/nope/messages",
    headers=admin_hdr,
)
check("unknown id in /messages → 404", r.status_code == 404)

# Non-admin attempting to hit /messages is also 403.
r = client.get(
    "/api/admin/sessions/sess-b1/messages",
    headers={"X-Test-User": "bob", "X-Roles": "user"},
)
check(
    "non-admin on /messages → 403",
    r.status_code == 403,
)


# ---------------------------------------------------------------------------
# T9. Validation: limit/offset bounds.
# ---------------------------------------------------------------------------
print("\n[T9] query-param validation")

r = client.get(
    "/api/admin/sessions?limit=0",
    headers=admin_hdr,
)
check("limit=0 rejected (422)", r.status_code == 422)

r = client.get(
    "/api/admin/sessions?limit=1000",
    headers=admin_hdr,
)
check("limit=1000 rejected (max 500)", r.status_code == 422)


# ---------------------------------------------------------------------------
# T10. Concurrency — two simultaneous admin requests must not cross-talk.
# Use two separate TestClients; they share the same app object and so the
# same memoised store (proving the per-request context isolation is real).
# ---------------------------------------------------------------------------
print("\n[T10] concurrent admin requests don't leak principal state")

import threading  # noqa: E402

results: dict[str, int] = {}


def _hit(tag: str, user: str, role: str) -> None:
    c = TestClient(app)
    r = c.get(
        "/api/admin/sessions",
        headers={
            "X-Test-User": user,
            "X-User-Id": user,
            "X-Roles": role,
        },
    )
    results[tag] = r.status_code


threads = [
    threading.Thread(target=_hit, args=("admin1", "root", "admin")),
    threading.Thread(target=_hit, args=("user1", "eve", "user")),
    threading.Thread(target=_hit, args=("admin2", "su", "admin")),
    threading.Thread(target=_hit, args=("user2", "mallory", "user")),
]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("concurrent admin1 → 200", results.get("admin1") == 200,
      detail=str(results.get("admin1")))
check("concurrent user1 → 403", results.get("user1") == 403,
      detail=str(results.get("user1")))
check("concurrent admin2 → 200", results.get("admin2") == 200)
check("concurrent user2 → 403", results.get("user2") == 403)


# ---------------------------------------------------------------------------
# T11. Naming hygiene on the new source files.
# ---------------------------------------------------------------------------
print("\n[T11] naming hygiene")
forbidden = [chr(111) + "penclaw", "x" + "claw", "her" + "mes"]
new_files = [
    SRC / "hubos/app/routers/admin_sessions.py",
]
for f in new_files:
    blob = f.read_text(encoding="utf-8").lower()
    for bad in forbidden:
        check(f"{f.name} clean of {bad!r}", bad not in blob)


# ---------------------------------------------------------------------------
shutil.rmtree(_TMP_ROOT, ignore_errors=True)
print("\n" + "=" * 64)
print(f"Result: {'ALL PASSED' if FAIL == 0 else f'{FAIL} FAILED'}")
print("=" * 64)
sys.exit(0 if FAIL == 0 else 1)
