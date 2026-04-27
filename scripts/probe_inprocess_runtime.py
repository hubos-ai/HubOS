#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：验证 hubos.core 在同一进程里能否跑通 task_store.create_task → orchestrator.execute_task → 终态。

不调任何 HTTP，直接用 hubos.core 的 in-process API。这是 sa-4 改造前的可行性验证。

通过条件：
  1. feature flag enable_execution_loop_mvp 强制为 True
  2. get_task_store / get_event_store / get_orchestrator 能拿到单例
  3. task_store.create_task 返回带 task_id 的对象
  4. orchestrator.execute_task(task_id) 同步跑完不抛
  5. 终态在 {done, failed, cancelled} 之一
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# 加 src 到 PYTHONPATH
SRC = Path("/Users/allen/HubOS/src")
sys.path.insert(0, str(SRC))

# 必须在 import hubos.core 之前设置
os.environ["ENABLE_EXECUTION_LOOP_MVP"] = "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("probe")


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    step("1. Import & wire singletons")
    from hubos.core.execution import (
        get_task_store,
        get_event_store,
        get_orchestrator,
    )
    from hubos.core.infra.feature_flags import get_feature_flags

    flags = get_feature_flags()
    flags.enable_execution_loop_mvp = True
    print(f"  enable_execution_loop_mvp = {flags.enable_execution_loop_mvp}")

    task_store = get_task_store()
    event_store = get_event_store()
    orchestrator = get_orchestrator()
    print(f"  task_store    : {type(task_store).__name__}")
    print(f"  event_store   : {type(event_store).__name__}")
    print(f"  orchestrator  : {type(orchestrator).__name__}")

    step("2. create_task")
    task = task_store.create_task(
        input_text="What is 2 + 2? Answer briefly.",
        session_id="probe-session-1",
        channel="probe",
        priority="normal",
        requested_workflow="one_person_default",
    )
    print(f"  task_id        : {task.task_id}")
    print(f"  trace_id       : {task.trace_id}")
    print(f"  initial status : {task.current_status.value}")

    step(
        "3. execute_task (synchronous in this probe; sa-4 will move to a thread)",
    )
    t0 = time.time()
    try:
        orchestrator.execute_task(task.task_id)
        elapsed = time.time() - t0
        print(f"  execute_task returned in {elapsed:.2f}s")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"  FAIL: execute_task raised: {type(e).__name__}: {e}")
        return 1

    step("4. Inspect terminal state")
    final_task = task_store.get_task(task.task_id)
    print(f"  final status : {final_task.current_status.value}")
    final_response = getattr(final_task, "final_response", None)
    print(f"  final_response present : {final_response is not None}")
    if final_response:
        text = (
            final_response
            if isinstance(final_response, str)
            else repr(final_response)[:300]
        )
        print(f"  final_response (first 300 chars):\n    {text[:300]}")

    step("5. Inspect events in EventStore")
    events = (
        event_store.get_events_after(task.task_id, last_seq=0)
        if hasattr(event_store, "get_events_after")
        else []
    )
    if not events:
        # try alternative API
        for attr in ("get_events", "list_events", "get_all_events"):
            fn = getattr(event_store, attr, None)
            if fn:
                try:
                    events = (
                        fn(task.task_id) if attr != "get_all_events" else fn()
                    )
                    break
                except Exception:
                    continue
    print(
        f"  events emitted : {len(events) if hasattr(events, '__len__') else '(non-listable)'}",
    )
    if events and hasattr(events, "__iter__"):
        for ev in list(events)[:5]:
            event_type = getattr(ev, "event_type", None) or (
                ev.get("event_type") if isinstance(ev, dict) else None
            )
            print(f"    - {event_type}")

    final_status = final_task.current_status.value.lower()
    if final_status in {"done", "failed", "cancelled"}:
        print(
            f"\n========== PROBE PASSED (terminal status: {final_status}) ==========",
        )
        return 0
    else:
        print(f"\n  FAIL: status not terminal: {final_status}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
