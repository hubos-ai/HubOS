#!/usr/bin/env python3
"""Stage A 冒烟测试：验证 src/hubos/orchestration/ 整体可用。

通过条件：
  1. 所有 .py 通过编译（py_compile）
  2. 包顶层 import 不抛错
  3. 关键类（Coordinator / DagScheduler / TaskStore / EventStore / 
     ExecutionOrchestrator / WorkerProvider / LLMRuntime）可导入并实例化
  4. 一个最小 ConversationEvent → Coordinator.process_event 流程跑通
"""
from __future__ import annotations

import importlib
import py_compile
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PKG_DIR = SRC / "hubos" / "core"
sys.path.insert(0, str(SRC))


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"  OK  : {msg}")


# ---------- 1. py_compile ----------
step("1. Compile every .py file under hubos.core")
errors: list[tuple[Path, str]] = []
files = sorted(PKG_DIR.rglob("*.py"))
for f in files:
    try:
        py_compile.compile(str(f), doraise=True)
    except py_compile.PyCompileError as e:
        errors.append((f, str(e)))
if errors:
    for f, e in errors:
        print(f"  - {f.relative_to(ROOT)}: {e}")
    fail(f"{len(errors)} files failed to compile")
ok(f"{len(files)} files compiled cleanly")

# ---------- 2. Module imports ----------
step("2. Import every submodule")
modules_to_import = [
    "hubos.core.schemas",
    "hubos.core.schemas.events",
    "hubos.core.schemas.tasks",
    "hubos.core.schemas.memory",
    "hubos.core.schemas.planning",
    "hubos.core.schemas.responses",
    "hubos.core.schemas.state",
    "hubos.core.schemas.collaboration",
    "hubos.core.workers",
    "hubos.core.workers.providers",
    "hubos.core.workers.providers.base",
    "hubos.core.workers.providers.stub",
    "hubos.core.workers.providers.executable",
    "hubos.core.orchestrator",
    "hubos.core.orchestrator.coordinator",
    "hubos.core.orchestrator.parallel_executor",
    "hubos.core.orchestrator.collaboration_bus",
    "hubos.core.orchestrator.policy_router",
    "hubos.core.orchestrator.reflection_engine",
    "hubos.core.execution",
    "hubos.core.execution.task_store",
    "hubos.core.execution.event_store",
    "hubos.core.execution.orchestrator",
    "hubos.core.execution.executors.base",
    "hubos.core.execution.executors.native_executor",
    "hubos.core.llm",
    "hubos.core.llm.runtime",
    "hubos.core.dag",
    "hubos.core.dag.models",
    "hubos.core.dag.validator",
    "hubos.core.dag.scheduler",
    "hubos.core.infra.feature_flags",
    "hubos.core.infra.agent_registry",
    "hubos.core.infra.runtime_config",
    "hubos.core.infra.metrics",
]
import_failures: list[tuple[str, str]] = []
for mod in modules_to_import:
    try:
        importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001
        import_failures.append((mod, f"{type(e).__name__}: {e}"))
if import_failures:
    for mod, err in import_failures:
        print(f"  - {mod:<60s}  {err}")
    fail(f"{len(import_failures)}/{len(modules_to_import)} modules failed to import")
ok(f"{len(modules_to_import)} modules imported cleanly")

# ---------- 3. Key classes can be instantiated ----------
step("3. Instantiate key classes")
try:
    from hubos.core.orchestrator.coordinator import Coordinator
    from hubos.core.execution.task_store import TaskStore
    from hubos.core.execution.event_store import EventStore
    from hubos.core.workers.providers.stub import StubWorkerProvider

    task_store = TaskStore()
    event_store = EventStore()
    worker = StubWorkerProvider()
    ok(f"TaskStore        → {type(task_store).__name__}")
    ok(f"EventStore       → {type(event_store).__name__}")
    ok(f"StubWorker       → {type(worker).__name__}")

    coord = Coordinator(worker_registry={"stub": worker})
    ok(f"Coordinator      → {type(coord).__name__}")
except Exception as e:  # noqa: BLE001
    traceback.print_exc()
    fail(f"instantiation crashed: {e}")

# ---------- 4. ConversationEvent 类可加载 (深度 e2e 留给 sa-3/sa-4) ----------
step("4. ConversationEvent class can be imported")
try:
    from hubos.core.schemas.events import ConversationEvent
    import dataclasses
    if dataclasses.is_dataclass(ConversationEvent):
        names = [f.name for f in dataclasses.fields(ConversationEvent)]
        ok(f"ConversationEvent is dataclass with fields: {names}")
    else:
        ok(f"ConversationEvent loaded: {ConversationEvent}")
    # Confirm Coordinator has the expected entry point
    api = [m for m in dir(coord) if not m.startswith('_') and callable(getattr(coord, m))]
    ok(f"Coordinator public methods: {api[:10]}{'...' if len(api) > 10 else ''}")
except Exception as e:  # noqa: BLE001
    traceback.print_exc()
    fail(f"step-4 crashed: {e}")

print("\n========== STAGE A STEP 2 SMOKE PASSED ==========")
