# -*- coding: utf-8 -*-
"""Stage B step 3: end-to-end wiring test.

Verifies that:
  1. ``hubos.agents.tools.__init__`` re-exports the 6 new GM tools.
  2. ``hubos.agents.react_agent`` imports them AND registers them in its
     ``tool_functions`` dict (the map the GM toolkit iterates over).
  3. ``hubos.app._app`` startup wires a ``hubos.core.workers.HostAgentRunner``
     by calling ``set_host_agent_runner(build_host_agent_runner(...))``, and
     a fake workspace round-trips cleanly through it.
  4. Clearing the registry returns ``get_host_agent_runner()`` to ``None``.

Because the real ``_app.lifespan`` drags in fastapi + every router + every
provider, we do NOT execute the full lifespan. Instead we replicate the two
critical lines verbatim (the same ``from hubos.core.workers import
set_host_agent_runner`` + ``from ..integrations import
build_host_agent_runner`` + ``set_host_agent_runner(build_host_agent_runner(
multi_agent_manager.get_agent))`` triplet), which is what sb-3b actually
contributes.

Run: python3 scripts/test_gm_tools_wiring.py
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Stub agentscope + agentscope_runtime so downstream imports don't explode.
# ---------------------------------------------------------------------------
class _StubTextBlock:
    def __init__(
        self,
        type: str = "text",
        text: str = "",
    ) -> None:  # noqa: A002
        self.type = type
        self.text = text


class _StubToolResponse:
    def __init__(self, content):
        self.content = content


class _StubMsg:
    def __init__(self, name: str, content, role: str = "user") -> None:
        self.name = name
        self.content = content
        self.role = role


class _StubAgentRequest:
    def __init__(self, **kwargs) -> None:
        self.input = kwargs.get("input", [])
        for k, v in kwargs.items():
            setattr(self, k, v)


def _install_stubs() -> None:
    _agentscope = types.ModuleType("agentscope")
    _msg_mod = types.ModuleType("agentscope.message")
    _msg_mod.TextBlock = _StubTextBlock
    _msg_mod.Msg = _StubMsg
    _tool_mod = types.ModuleType("agentscope.tool")
    _tool_mod.ToolResponse = _StubToolResponse
    _agentscope.message = _msg_mod
    _agentscope.tool = _tool_mod

    sys.modules["agentscope"] = _agentscope
    sys.modules["agentscope.message"] = _msg_mod
    sys.modules["agentscope.tool"] = _tool_mod

    _runtime = types.ModuleType("agentscope_runtime")
    _engine = types.ModuleType("agentscope_runtime.engine")
    _schemas = types.ModuleType("agentscope_runtime.engine.schemas")
    _agent_schemas = types.ModuleType(
        "agentscope_runtime.engine.schemas.agent_schemas",
    )
    _agent_schemas.AgentRequest = _StubAgentRequest
    _schemas.agent_schemas = _agent_schemas
    _engine.schemas = _schemas
    _runtime.engine = _engine

    sys.modules["agentscope_runtime"] = _runtime
    sys.modules["agentscope_runtime.engine"] = _engine
    sys.modules["agentscope_runtime.engine.schemas"] = _schemas
    sys.modules[
        "agentscope_runtime.engine.schemas.agent_schemas"
    ] = _agent_schemas


_install_stubs()


def _load_source(name: str, rel_path: str):
    """Execute a single .py file as a top-level module without importing its
    heavy parent package."""
    full = SRC / rel_path
    spec = importlib.util.spec_from_file_location(name, full)
    assert spec and spec.loader, name
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAIL
    print(
        f"  [{'OK' if cond else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}",
    )
    if not cond:
        FAIL += 1


# ---------------------------------------------------------------------------
# T1. Package re-exports (tools/__init__.py).
# ---------------------------------------------------------------------------
print("\n[T1] hubos.agents.tools re-exports 6 new tools")
# Importing the package __init__ will try to pull react_agent siblings via
# tool_guard_mixin etc. — so we only load the __init__ file itself as a
# stand-alone module.

# First stub out hubos.agents.tools.runtime_delegate because the real one
# imports httpx (optional) and we don't need its runtime behaviour here.
_rd_stub = types.ModuleType("hubos.agents.tools.runtime_delegate")
_rd_stub.cancel_task = lambda *a, **k: None
_rd_stub.delegate_task = lambda *a, **k: None
_rd_stub.set_runtime_request_context = lambda *a, **k: None
_rd_stub.track_task = lambda *a, **k: None
_rd_stub._current_runtime_ctx = lambda: {}
sys.modules["hubos.agents.tools.runtime_delegate"] = _rd_stub

# Also stub the AgentScope-tool-based siblings we don't need to register.
for sibling in [
    "hubos.agents.tools.file_io",
    "hubos.agents.tools.file_search",
    "hubos.agents.tools.shell",
    "hubos.agents.tools.send_file",
    "hubos.agents.tools.browser_control",
    "hubos.agents.tools.desktop_screenshot",
    "hubos.agents.tools.view_media",
    "hubos.agents.tools.memory_search",
    "hubos.agents.tools.get_current_time",
    "hubos.agents.tools.get_token_usage",
]:
    mod = types.ModuleType(sibling)
    # Provide common names the __init__ tries to star-import.
    for attr in (
        "read_file",
        "write_file",
        "edit_file",
        "append_file",
        "grep_search",
        "glob_search",
        "execute_shell_command",
        "send_file_to_user",
        "browser_use",
        "desktop_screenshot",
        "view_image",
        "view_video",
        "create_memory_search_tool",
        "get_current_time",
        "set_user_timezone",
        "get_token_usage",
    ):
        setattr(mod, attr, lambda *a, **k: None)
    sys.modules[sibling] = mod

# agentscope.tool also exports execute_python_code / view_text_file /
# write_text_file — add them to the stub.
sys.modules["agentscope.tool"].execute_python_code = lambda *a, **k: None
sys.modules["agentscope.tool"].view_text_file = lambda *a, **k: None
sys.modules["agentscope.tool"].write_text_file = lambda *a, **k: None

# Create hubos + hubos.agents + hubos.agents.tools package hierarchy so
# relative imports inside the __init__.py resolve.
for pkg in ("hubos", "hubos.agents", "hubos.agents.tools"):
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(SRC / pkg.replace(".", "/"))]
        sys.modules[pkg] = m

tools_init = _load_source(
    "hubos.agents.tools",
    "hubos/agents/tools/__init__.py",
)

for tool_name in (
    "spawn_subagents",
    "coordinate_workflow",
    "track_workflow",
    "cancel_workflow",
    "recall_long_term",
    "recall_session",
):
    check(
        f"tools.{tool_name} re-exported",
        hasattr(tools_init, tool_name) and tool_name in tools_init.__all__,
    )


# ---------------------------------------------------------------------------
# T2. react_agent.py tool_functions dict contains the 6 new entries.
# ---------------------------------------------------------------------------
print("\n[T2] react_agent.tool_functions dict registers 6 new tools")

rax_src = (SRC / "hubos/agents/react_agent.py").read_text(encoding="utf-8")

# Slice out the tool_functions literal region for a focused check that
# doesn't require fully importing the module (which would pull in
# memory, channels, model config, ...).
start = rax_src.find("tool_functions = {")
end = rax_src.find("}", start)
block = rax_src[start : end + 1] if start != -1 else ""

for mapping in (
    '"spawn_subagents": spawn_subagents',
    '"coordinate_workflow": coordinate_workflow',
    '"track_workflow": track_workflow',
    '"cancel_workflow": cancel_workflow',
    '"recall_long_term": recall_long_term',
    '"recall_session": recall_session',
):
    check(f"tool_functions has {mapping}", mapping in block)

# Also confirm the imports are present at module level.
for imp in (
    "spawn_subagents,",
    "coordinate_workflow,",
    "track_workflow,",
    "cancel_workflow,",
    "recall_long_term,",
    "recall_session,",
):
    check(f"react_agent imports {imp.rstrip(',')}", imp in rax_src)


# ---------------------------------------------------------------------------
# T3. _app.py startup wiring: set_host_agent_runner is called with a
#     working adapter bound to MultiAgentManager.get_agent.
# ---------------------------------------------------------------------------
print("\n[T3] _app.py startup wiring — HostAgentRunner round-trip")

# Fresh registry.
from hubos.core.workers import (  # noqa: E402
    clear_host_agent_runner,
    get_host_agent_runner,
    set_host_agent_runner,
)

clear_host_agent_runner()
check("registry starts empty", get_host_agent_runner() is None)

# Fake a MultiAgentManager-like object whose .get_agent returns a workspace
# with the minimal .runner.query_handler + .session_id / .user_id surface.


class _FakeRunner:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def query_handler(self, msgs, request):  # noqa: ARG002
        msg = types.SimpleNamespace(
            name="A",
            role="assistant",
            content=[_StubTextBlock("text", self._reply)],
        )
        yield msg, True


class _FakeWorkspace:
    def __init__(self, reply: str) -> None:
        self.runner = _FakeRunner(reply)
        self.session_id = "s0"
        self.user_id = "u0"


class _FakeManager:
    def __init__(self) -> None:
        self._map = {"alpha": _FakeWorkspace("hello from alpha")}

    async def get_agent(self, agent_id: str):
        return self._map[agent_id]


mgr = _FakeManager()

# Replicate exactly what _app.py does at startup (sb-3b).
from hubos.integrations import build_host_agent_runner  # noqa: E402

set_host_agent_runner(build_host_agent_runner(mgr.get_agent))
check("registry populated after wiring", get_host_agent_runner() is not None)

# Call through: build an env-less prompt -> agent alpha -> final text.
runner = get_host_agent_runner()


async def _roundtrip():
    out = await runner(
        "alpha",
        "please say hi",
        {"session_id": "sess-xyz", "user_id": "u0"},
    )
    return out


result = asyncio.run(_roundtrip())
check(
    "round-trip returns assistant text",
    result == "hello from alpha",
    detail=f"got {result!r}",
)


# ---------------------------------------------------------------------------
# T4. Shutdown clears the runner (matches the finally-block in _app.py).
# ---------------------------------------------------------------------------
print("\n[T4] shutdown path clears registry")
clear_host_agent_runner()
check("registry empty after clear", get_host_agent_runner() is None)


# ---------------------------------------------------------------------------
# T5. Naming hygiene: new wiring code doesn't leak forbidden project names.
# ---------------------------------------------------------------------------
print("\n[T5] naming hygiene on modified files")

_app_src = (SRC / "hubos/app/_app.py").read_text(encoding="utf-8")
tools_init_src = (SRC / "hubos/agents/tools/__init__.py").read_text(
    encoding="utf-8",
)

FORBIDDEN = ("openclaw", "hermes", "xclaw")
for blob_name, blob in [
    ("_app.py", _app_src),
    ("tools/__init__.py", tools_init_src),
    ("react_agent.py", rax_src),
]:
    for bad in FORBIDDEN:
        check(
            f"{blob_name} clean of {bad!r}",
            bad not in blob.lower(),
        )


# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"Result: {'ALL PASSED' if FAIL == 0 else f'{FAIL} FAILED'}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
