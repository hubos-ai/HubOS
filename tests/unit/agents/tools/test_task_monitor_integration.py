# -*- coding: utf-8 -*-
"""Integration tests for TaskMonitorStore instrumentation in multi-agent tools.

Tests verify that spawn_subagents / coordinate_workflow / delegate_task emit
the correct monitoring events, and that monitoring failures never affect tool
return values.

NOTE: agentscope is not installed in the unit-test venv. We mock the required
agentscope submodules before importing the tools under test.
"""
from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hubos.app.task_monitor import TaskEventType, TaskMonitorStore, TaskStatus
from hubos.app.task_monitor_helpers import get_monitor_store


# ---------------------------------------------------------------------------
# Mock agentscope before any hubos.agents.tools import
# ---------------------------------------------------------------------------


def _install_agentscope_mocks():
    """Ensure agentscope stubs exist so tool modules can be imported.

    We MUST replace ToolResponse and TextBlock even if the real agentscope
    package is already loaded (e.g. because router tests imported it first),
    because the test assertions depend on the mock object layout (.text attr).
    """
    # Save originals for restoration if they exist
    _saved: dict[str, tuple[object, object]] = {}
    for key in (
        "agentscope.tool.ToolResponse",
        "agentscope.message.TextBlock",
    ):
        mod_name, attr = key.rsplit(".", 1)
        if mod_name in sys.modules and hasattr(sys.modules[mod_name], attr):
            _saved[key] = (
                sys.modules[mod_name],
                getattr(sys.modules[mod_name], attr),
            )

    # Create all required agentscope submodules
    for mod_name in (
        "agentscope",
        "agentscope.tool",
        "agentscope.message",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    # agentscope.tool: needs ToolResponse + tool functions imported by __init__.py
    # Always replace — real agentscope ToolResponse has a different content layout.
    _tool = sys.modules["agentscope.tool"]

    class _ToolResponse:
        def __init__(self, content=None):
            self.content = content or []

    _tool.ToolResponse = _ToolResponse
    # Stub the tool functions imported by hubos.agents.tools.__init__
    for name in (
        "execute_python_code",
        "view_text_file",
        "write_text_file",
    ):
        if not hasattr(_tool, name):
            setattr(_tool, name, lambda *a, **kw: None)

    # agentscope.message: needs TextBlock — always replace.
    _msg = sys.modules["agentscope.message"]

    class _TextBlock:
        def __init__(self, **kw):
            self.type = kw.get("type", "text")
            self.text = kw.get("text", "")

    _msg.TextBlock = _TextBlock

    # Also mock agentscope.icons if needed
    for mod_name in ("agentscope.icons",):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    return _saved


def _restore_agentscope_mocks(
    _saved: dict[str, tuple[object, object]]
) -> None:
    """Restore original agentscope classes after test module is loaded."""
    for key, (mod, cls) in _saved.items():
        _, attr = key.rsplit(".", 1)
        setattr(mod, attr, cls)


def _install_heavy_dep_mocks():
    """Mock the heavy dependencies that hubos.agents.tools.__init__ triggers."""
    # Mock shortuuid before config is imported
    if "shortuuid" not in sys.modules:
        _su = ModuleType("shortuuid")
        _su.uuid = lambda: "mock-uuid"
        sys.modules["shortuuid"] = _su


_agentscope_saved = _install_agentscope_mocks()
_install_heavy_dep_mocks()

import importlib.util as _ilu


def _import_tool_module(dotted_name: str):
    """Import a tool module bypassing hubos.agents.tools.__init__.

    Uses importlib.util to load the file directly, avoiding the
    agentscope/shortuuid dependency chain in the package __init__.py.
    """
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]

    # Pre-load any sibling modules that the target imports via relative imports,
    # so the relative import resolves from sys.modules instead of triggering __init__.py
    _preload_sibling_modules(dotted_name)

    parts = dotted_name.split(".")
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[4]
    rel_path = Path(*parts[:-1]) / (parts[-1] + ".py")
    file_path = project_root / "src" / rel_path

    spec = _ilu.spec_from_file_location(dotted_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot find module {dotted_name} at {file_path}")
    mod = _ilu.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _preload_sibling_modules(dotted_name: str):
    """Pre-load sibling modules into sys.modules to avoid __init__.py.

    When agent_workforce.py does `from .runtime_delegate import ...`, Python
    needs `hubos.agents.tools.runtime_delegate` in sys.modules. We load it
    directly from file so the package __init__.py never runs.
    """
    from pathlib import Path

    package = ".".join(dotted_name.split(".")[:-1])  # hubos.agents.tools
    project_root = Path(__file__).resolve().parents[4]
    pkg_dir = project_root / "src" / Path(*package.split("."))

    # Map of modules the tools import from each other
    sibling_files = {
        f"{package}.runtime_delegate": pkg_dir / "runtime_delegate.py",
    }

    for sibling_name, sibling_path in sibling_files.items():
        if sibling_name in sys.modules:
            continue
        if not sibling_path.exists():
            continue
        spec = _ilu.spec_from_file_location(sibling_name, str(sibling_path))
        if spec is None or spec.loader is None:
            continue
        mod = _ilu.module_from_spec(spec)
        sys.modules[sibling_name] = mod
        spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeWorkerResult:
    def __init__(self, content: str = "ok", ms: int = 10):
        self.data = {"content": content}
        self.execution_time_ms = ms


async def _fake_execute_success(self, **kw):
    return _FakeWorkerResult(
        f"result from {getattr(self, 'agent_id', 'unknown')}", 15
    )


async def _fake_execute_fail(self, **kw):
    raise RuntimeError("AgentError: boom")


def _make_mock_runner():
    """Return a mock HostAgentRunner."""
    return MagicMock()


def _patch_worker(mod, execute_fn):
    """Return a patch that makes HostAgentWorker(...) return a mock with execute=execute_fn."""

    def _make_worker(*args, **kwargs):
        w = MagicMock()
        w.agent_id = kwargs.get("agent_id", "mock-agent")
        w.execute = lambda **kw: execute_fn(w, **kw)
        return w

    return patch.object(mod, "HostAgentWorker", side_effect=_make_worker)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_monitor_store():
    """Reset the global monitor store and ensure agentscope mocks are active."""
    import hubos.app.task_monitor_helpers as _helpers

    old = _helpers._store
    _helpers._store = None

    # Re-install agentscope mocks — other test modules (e.g. router tests)
    # may have loaded the real agentscope package into sys.modules.
    _install_agentscope_mocks()

    # Evict cached tool modules so they re-import with fresh mocks.
    for key in list(sys.modules):
        if (
            key.startswith("hubos.agents.tools.")
            and key != "hubos.agents.tools"
        ):
            del sys.modules[key]

    yield
    _helpers._store = old


@pytest.fixture
def store() -> TaskMonitorStore:
    """Return the fresh global monitor store."""
    return get_monitor_store()


# ---------------------------------------------------------------------------
# spawn_subagents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_subagents_creates_task_and_stage_events(
    store: TaskMonitorStore,
):
    """spawn_subagents should create a monitor task and emit stage events."""
    _mod = _import_tool_module("hubos.agents.tools.agent_workforce")
    spawn_subagents = _mod.spawn_subagents

    with patch.object(
        _mod, "get_host_agent_runner", return_value=_make_mock_runner()
    ), _patch_worker(_mod, _fake_execute_success):
        result = await spawn_subagents(
            assignments=[
                {"agent_id": "agent-a", "prompt": "do A", "label": "task_a"},
                {"agent_id": "agent-b", "prompt": "do B", "label": "task_b"},
            ],
        )

    # Original tool response unchanged
    data = json.loads(result.content[0].text)
    assert data["succeeded"] == 2
    assert data["failed"] == 0

    # Monitor store has a task
    tasks = await store.list_tasks(tool_name="spawn_subagents")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == TaskStatus.DONE
    assert task.progress == 100
    assert task.tool_name == "spawn_subagents"

    # Events: task_created + task_updated(running) + 2*(stage_started+stage_completed) + task_updated(done)
    event_types = [e.event_type for e in task.events]
    assert TaskEventType.TASK_CREATED in event_types
    assert event_types.count(TaskEventType.STAGE_STARTED) == 2
    assert event_types.count(TaskEventType.STAGE_COMPLETED) == 2


@pytest.mark.asyncio
async def test_spawn_subagents_partial_failure(store: TaskMonitorStore):
    """When some sub-agents fail, monitor should record error events and FAILED status."""
    _mod = _import_tool_module("hubos.agents.tools.agent_workforce")
    spawn_subagents = _mod.spawn_subagents

    call_count = 0

    async def _execute_intermittent(self, **kw):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            raise RuntimeError("AgentError: boom")
        return _FakeWorkerResult(f"result from {self.agent_id}", 15)

    with patch.object(
        _mod, "get_host_agent_runner", return_value=_make_mock_runner()
    ), _patch_worker(_mod, _execute_intermittent):
        result = await spawn_subagents(
            assignments=[
                {"agent_id": "agent-a", "prompt": "do A"},
                {"agent_id": "agent-b", "prompt": "do B"},
            ],
        )

    data = json.loads(result.content[0].text)
    assert data["succeeded"] == 1
    assert data["failed"] == 1

    tasks = await store.list_tasks(tool_name="spawn_subagents")
    task = tasks[0]
    assert task.status == TaskStatus.FAILED
    event_types = [e.event_type for e in task.events]
    assert TaskEventType.ERROR in event_types


# ---------------------------------------------------------------------------
# coordinate_workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinate_workflow_creates_task_with_workflow_id(
    store: TaskMonitorStore,
):
    """coordinate_workflow should create a monitor task and record workflow_id."""
    _mod = _import_tool_module("hubos.agents.tools.agent_workforce")
    coordinate_workflow = _mod.coordinate_workflow

    with patch.object(
        _mod, "get_host_agent_runner", return_value=_make_mock_runner()
    ), _patch_worker(_mod, _fake_execute_success):
        result = await coordinate_workflow(
            steps=[
                {"id": "step1", "agent_id": "agent-a", "prompt": "do step 1"},
            ],
            title="Test workflow",
            summary_step_id="step1",
        )

    data = json.loads(result.content[0].text)
    assert data["status"] == "done"
    assert "workflow_id" in data

    # Monitor store has a task
    tasks = await store.list_tasks(tool_name="coordinate_workflow")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == TaskStatus.DONE
    assert task.tool_name == "coordinate_workflow"
    assert task.metadata is not None
    assert "workflow_id" in task.metadata
    assert task.result_summary is not None


@pytest.mark.asyncio
async def test_coordinate_workflow_step_failure(store: TaskMonitorStore):
    """coordinate_workflow with a failing step should emit error events."""
    _mod = _import_tool_module("hubos.agents.tools.agent_workforce")
    coordinate_workflow = _mod.coordinate_workflow

    with patch.object(
        _mod, "get_host_agent_runner", return_value=_make_mock_runner()
    ), _patch_worker(_mod, _fake_execute_fail):
        result = await coordinate_workflow(
            steps=[
                {"id": "step1", "agent_id": "agent-a", "prompt": "fail here"},
            ],
        )

    data = json.loads(result.content[0].text)
    assert data["status"] == "failed"

    tasks = await store.list_tasks(tool_name="coordinate_workflow")
    task = tasks[0]
    assert task.status == TaskStatus.FAILED
    event_types = [e.event_type for e in task.events]
    assert TaskEventType.STAGE_STARTED in event_types
    assert TaskEventType.ERROR in event_types


# ---------------------------------------------------------------------------
# delegate_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_task_creates_task_with_runtime_mode(
    store: TaskMonitorStore,
):
    """delegate_task should create a monitor task and record runtime mode."""
    _rd = _import_tool_module("hubos.agents.tools.runtime_delegate")
    _aw = _import_tool_module("hubos.agents.tools.agent_workforce")
    delegate_task = _rd.delegate_task

    # Mock agent_bridge mode with a single-agent spawn
    with patch.dict(
        "os.environ",
        {"HUBOS_DELEGATE_AGENT_BRIDGE": "1"},
    ), patch.object(
        _aw, "get_host_agent_runner", return_value=_make_mock_runner()
    ), _patch_worker(
        _aw, _fake_execute_success
    ):
        result = await delegate_task(goal="Research pricing for product X")

    text = result.content[0].text
    assert "Task ID:" in text

    # Monitor store has a task
    tasks = await store.list_tasks(tool_name="delegate_task")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.tool_name == "delegate_task"
    assert task.status == TaskStatus.DONE
    assert task.metadata is not None
    assert task.metadata.get("runtime_mode") == "agent_bridge"


@pytest.mark.asyncio
async def test_delegate_task_empty_goal(store: TaskMonitorStore):
    """delegate_task with empty goal returns error, no monitor task."""
    _mod = _import_tool_module("hubos.agents.tools.runtime_delegate")
    delegate_task = _mod.delegate_task

    result = await delegate_task(goal="")
    assert "cannot be empty" in result.content[0].text

    tasks = await store.list_tasks()
    assert len(tasks) == 0


# ---------------------------------------------------------------------------
# Monitoring failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_failure_does_not_affect_spawn_subagents(
    store: TaskMonitorStore,
):
    """If the monitor store throws, spawn_subagents still returns normally."""
    _mod = _import_tool_module("hubos.agents.tools.agent_workforce")
    spawn_subagents = _mod.spawn_subagents

    # Make the store's create_task throw
    original_create = store.create_task

    async def _failing_create(*a, **kw):
        raise RuntimeError("monitor store broken")

    store.create_task = _failing_create

    with patch.object(
        _mod, "get_host_agent_runner", return_value=_make_mock_runner()
    ), _patch_worker(_mod, _fake_execute_success):
        result = await spawn_subagents(
            assignments=[
                {"agent_id": "agent-a", "prompt": "do A"},
            ],
        )

    # Tool still returns successfully
    data = json.loads(result.content[0].text)
    assert data["succeeded"] == 1

    # Restore
    store.create_task = original_create
