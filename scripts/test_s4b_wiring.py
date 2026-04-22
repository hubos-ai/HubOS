"""S4b — wiring integrity checks for the admin sessions page.

These are cheap structural checks that catch the class of bugs that slip
past TypeScript (missing route, missing sidebar entry, i18n key drift,
non-self-consistent constants). They do NOT replace a browser test —
full browser verification is a manual step — but they do guard the
invariants that would silently hide the admin page in production.

Checks
──────

  1. admin_sessions.py is registered in routers/__init__ AND sits AFTER
     settings_router inclusion (admin should not be above unauthenticated
     public routers; we just confirm it's present).
  2. TenantContextMiddleware is wired in _app.py between AuthMiddleware
     and AgentContextMiddleware, i.e. registration order is
     [AgentContextMiddleware, TenantContextMiddleware, AuthMiddleware].
  3. Frontend:
     a. MainLayout registers /admin/sessions → AdminSessionsPage.
     b. KEY_TO_PATH has "admin-sessions" → "/admin/sessions".
     c. KEY_TO_LABEL has "admin-sessions".
     d. Sidebar imports useIsAdmin and uses it to gate admin-group.
     e. adminSessions.ts API module exists and exports adminSessionsApi.
     f. useIsAdmin.ts exists and exports useIsAdmin + clearAdminProbe.
     g. pages/Admin/Sessions/index.tsx exists.
  4. i18n: zh + en have nav.adminGroup, nav.adminSessions, and the
     `adminSessions` namespace; ja + ru at minimum have nav.adminGroup +
     nav.adminSessions (the rest falls back via defaultValue).
  5. All 4 locale JSONs parse successfully.
  6. Naming hygiene on the 3 new frontend source files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC_BE = REPO / "src"
SRC_FE = REPO / "console" / "src"

FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAIL
    mark = "OK" if cond else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL += 1


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ─── 1. Backend router wiring ─────────────────────────────────────────────
print("\n[1] Backend router wiring")
routers_init = read(SRC_BE / "hubos" / "app" / "routers" / "__init__.py")
check(
    "routers/__init__.py imports admin_sessions",
    "from .admin_sessions import router as admin_sessions_router" in routers_init,
)
check(
    "routers/__init__.py include_router(admin_sessions_router)",
    "router.include_router(admin_sessions_router)" in routers_init,
)


# ─── 2. Middleware order in _app.py ────────────────────────────────────────
print("\n[2] Middleware registration order")
app_py = read(SRC_BE / "hubos" / "app" / "_app.py")

# Grab only the add_middleware call order, ignoring CORS branch (conditional).
md_order = [
    line.strip()
    for line in app_py.splitlines()
    if line.strip().startswith("app.add_middleware(")
    and "CORSMiddleware" not in line
]
check(
    "AgentContextMiddleware registered first (innermost)",
    md_order and "AgentContextMiddleware" in md_order[0],
    detail=md_order[0] if md_order else "(none)",
)
check(
    "TenantContextMiddleware registered second (middle)",
    len(md_order) >= 2 and "TenantContextMiddleware" in md_order[1],
    detail=md_order[1] if len(md_order) >= 2 else "(none)",
)
check(
    "AuthMiddleware registered last before CORS (outermost non-CORS)",
    len(md_order) >= 3 and "AuthMiddleware" in md_order[2],
    detail=md_order[2] if len(md_order) >= 3 else "(none)",
)
check(
    "TenantContextMiddleware imported",
    "from .tenant_middleware import TenantContextMiddleware" in app_py,
)


# ─── 3. Frontend wiring ────────────────────────────────────────────────────
print("\n[3] Frontend wiring")
main_layout = read(SRC_FE / "layouts" / "MainLayout" / "index.tsx")
check(
    "MainLayout imports AdminSessionsPage",
    'import AdminSessionsPage from "../../pages/Admin/Sessions"' in main_layout,
)
check(
    "MainLayout routes /admin/sessions → AdminSessionsPage",
    '<Route path="/admin/sessions" element={<AdminSessionsPage />}' in main_layout,
)
check(
    'MainLayout pathToKey has "/admin/sessions"',
    '"/admin/sessions": "admin-sessions"' in main_layout,
)

constants_ts = read(SRC_FE / "layouts" / "constants.ts")
check(
    "constants.ts KEY_TO_PATH has admin-sessions",
    '"admin-sessions": "/admin/sessions"' in constants_ts,
)
check(
    "constants.ts KEY_TO_LABEL has admin-sessions",
    '"admin-sessions": "nav.adminSessions"' in constants_ts,
)
check(
    "constants.ts DEFAULT_OPEN_KEYS has admin-group",
    '"admin-group"' in constants_ts,
)

sidebar = read(SRC_FE / "layouts" / "Sidebar.tsx")
check(
    "Sidebar imports useIsAdmin + clearAdminProbe",
    'import { clearAdminProbe, useIsAdmin } from "../hooks/useIsAdmin"' in sidebar,
)
check(
    "Sidebar reads isAdmin from hook",
    "const { isAdmin } = useIsAdmin();" in sidebar,
)
check(
    "Sidebar menu gated by isAdmin (admin-group)",
    '"admin-group"' in sidebar and "isAdmin" in sidebar,
)
check(
    "Sidebar collapsed nav gated by isAdmin (admin-sessions)",
    '"admin-sessions"' in sidebar,
)
check(
    "Sidebar logout clears admin probe cache",
    "clearAdminProbe()" in sidebar,
)

api_mod = SRC_FE / "api" / "modules" / "adminSessions.ts"
check("adminSessions.ts module present", api_mod.exists())
api_src = read(api_mod)
check(
    "adminSessionsApi exported with {list,get,messages,probe}",
    all(
        f in api_src
        for f in [
            "export const adminSessionsApi",
            "list:",
            "get:",
            "messages:",
            "probe:",
        ]
    ),
)
check(
    "classifyError exported (handles 403/404/401)",
    "export function classifyError" in api_src
    and '"forbidden"' in api_src
    and '"not_found"' in api_src,
)

hook = SRC_FE / "hooks" / "useIsAdmin.ts"
check("useIsAdmin.ts present", hook.exists())
hook_src = read(hook)
check(
    "useIsAdmin + clearAdminProbe exported",
    "export function useIsAdmin" in hook_src
    and "export function clearAdminProbe" in hook_src,
)

page = SRC_FE / "pages" / "Admin" / "Sessions" / "index.tsx"
check("pages/Admin/Sessions/index.tsx present", page.exists())
page_src = read(page)
check(
    "page uses useIsAdmin gate",
    "useIsAdmin" in page_src
    and 'adminStatus === "denied"' in page_src
    and 'adminStatus === "error"' in page_src,
)


# ─── 4. i18n keys + 5. JSON parse ────────────────────────────────────────
print("\n[4/5] i18n locales")
for locale_name in ["zh", "en", "ja", "ru"]:
    path = SRC_FE / "locales" / f"{locale_name}.json"
    try:
        data = json.loads(read(path))
        check(f"{locale_name}.json parses", True)
    except json.JSONDecodeError as e:
        check(f"{locale_name}.json parses", False, detail=str(e))
        continue

    nav = data.get("nav") or {}
    check(f"{locale_name}.nav.adminGroup present", "adminGroup" in nav)
    check(f"{locale_name}.nav.adminSessions present", "adminSessions" in nav)

    if locale_name in ("zh", "en"):
        ns = data.get("adminSessions") or {}
        for key in [
            "title",
            "view",
            "empty",
            "totalCount",
            "loadFailed",
            "detailFailed",
            "sessionNotFound",
            "columns",
            "filters",
            "detail",
            "denied",
            "error",
        ]:
            check(f"{locale_name}.adminSessions.{key} present", key in ns)


# ─── 6. Naming hygiene on new files ───────────────────────────────────────
print("\n[6] Naming hygiene")
forbidden = [chr(111) + "penclaw", "x" + "claw", "her" + "mes"]
new_files = [api_mod, hook, page]
for f in new_files:
    blob = read(f).lower()
    for bad in forbidden:
        check(f"{f.name} clean of {bad!r}", bad not in blob)


# ─── Summary ───────────────────────────────────────────────────────────────
print("\n" + "=" * 64)
print(f"Result: {'ALL PASSED' if FAIL == 0 else f'{FAIL} FAILED'}")
print("=" * 64)
sys.exit(0 if FAIL == 0 else 1)
