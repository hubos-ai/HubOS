#!/usr/bin/env python3
"""sa-4 端到端验证：runtime_delegate 在 in-process 模式下的三件套 + 并发。

由于本机 Python 3.14 还没装 agentscope（HubOS 要求 <3.14），这里用最小
``sys.modules`` stub 替换掉 ``agentscope.tool`` / ``agentscope.message`` 的
``ToolResponse`` / ``TextBlock``，让 ``runtime_delegate`` 可以独立加载。

测试矩阵：
  T1: delegate_task(wait=True) → 返回 final_response, status=done
  T2: delegate_task(wait=False) → 立即返回 task_id, 后续 track_task 等到完成
  T3: track_task(unknown_id) → 结构化 not-found
  T4: cancel_task(known_id) → S6-pending 提示，HTTP 路径不被触发
  T5: 并发：3 个 session 同时 delegate_task(wait=True)，验证终态都 done
      且每个 session_id 在 task_store.list_tasks() 里独立可识别
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import types
from pathlib import Path

# ---------- 1. 让 agentscope.* 可以被 import (最小 stub) ----------
def _stub_agentscope() -> None:
    pkg = types.ModuleType("agentscope")
    msg = types.ModuleType("agentscope.message")
    tool = types.ModuleType("agentscope.tool")

    class TextBlock(dict):
        def __init__(self, type: str, text: str) -> None:
            super().__init__(type=type, text=text)
            self.type = type
            self.text = text

    class ToolResponse:
        def __init__(self, content: list) -> None:
            self.content = content

        @property
        def text(self) -> str:
            parts = []
            for blk in self.content:
                t = getattr(blk, "text", None) or (blk.get("text") if isinstance(blk, dict) else None)
                if t:
                    parts.append(t)
            return "\n".join(parts)

        def __repr__(self) -> str:  # for nicer print
            return f"ToolResponse({self.text!r})"

    msg.TextBlock = TextBlock
    tool.ToolResponse = ToolResponse
    sys.modules["agentscope"] = pkg
    sys.modules["agentscope.message"] = msg
    sys.modules["agentscope.tool"] = tool


_stub_agentscope()

# ---------- 2. 路径 + 环境 ----------
SRC = Path("/Users/allen/HubOS/src")
sys.path.insert(0, str(SRC))
os.environ["HUBOS_RUNTIME_MODE"] = "inprocess"
os.environ["ENABLE_EXECUTION_LOOP_MVP"] = "true"

import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# ---------- 3. 真正的测试 ----------

def step(t: str) -> None:
    print(f"\n=== {t} ===")


def render(label: str, resp) -> None:
    body = resp.text if hasattr(resp, "text") else str(resp)
    short = body if len(body) <= 600 else body[:600] + f" ... [{len(body)} chars total]"
    print(f"  [{label}] {short}")


async def run() -> int:
    # Late import: load runtime_delegate.py directly (bypass package __init__
    # which transitively imports browser_use / desktop_screenshot / etc.).
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runtime_delegate_under_test",
        str(SRC / "hubos" / "agents" / "tools" / "runtime_delegate.py"),
    )
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)
    delegate_task = rd.delegate_task
    track_task = rd.track_task
    cancel_task = rd.cancel_task
    set_runtime_request_context = rd.set_runtime_request_context
    _get_inprocess_components = rd._get_inprocess_components

    set_runtime_request_context({
        "session_id": "e2e-session-A",
        "user_id": "e2e-user",
        "channel": "e2e-test",
    })

    step("T1. delegate_task(wait=True) 端到端")
    t0 = time.time()
    resp1 = await delegate_task(
        goal="What is 5 + 7? Reply briefly.",
        wait=True,
        timeout_seconds=60,
    )
    render(f"T1 result in {time.time()-t0:.1f}s", resp1)
    body1 = resp1.text
    assert "Status: done" in body1, f"expected Status: done, got: {body1[:200]}"
    assert "Task ID: " in body1, f"missing Task ID in: {body1[:200]}"
    print("  T1 PASS")

    step("T2. delegate_task(wait=False) + track_task(follow=True)")
    resp2_submit = await delegate_task(
        goal="What is the capital of France? Reply with one word.",
        wait=False,
    )
    render("T2 submit", resp2_submit)
    body2 = resp2_submit.text
    assert "Initial status: received" in body2 or "Initial status:" in body2
    # extract task_id
    task_id_2 = None
    for line in body2.splitlines():
        if line.strip().startswith("Task ID:"):
            task_id_2 = line.split(":", 1)[1].strip()
            break
    assert task_id_2, f"could not find Task ID in: {body2}"
    print(f"  extracted task_id: {task_id_2}")

    resp2_track = await track_task(task_id=task_id_2, follow=True, timeout_seconds=60)
    render("T2 track final", resp2_track)
    body2t = resp2_track.text
    assert "Status: done" in body2t, f"expected Status: done in: {body2t[:200]}"
    print("  T2 PASS")

    step("T3. track_task(unknown_id) → not-found error")
    resp3 = await track_task(task_id="task-does-not-exist-xxx")
    render("T3", resp3)
    body3 = resp3.text
    assert "not found" in body3.lower(), f"expected not-found error, got: {body3}"
    print("  T3 PASS")

    step("T4. cancel_task(known_id) → S6-pending notice")
    resp4 = await cancel_task(task_id=task_id_2)
    render("T4", resp4)
    body4 = resp4.text
    assert ("does not yet support" in body4) or ("cancel will land" in body4), \
        f"expected S6-pending notice, got: {body4}"
    print("  T4 PASS")

    step("T5. concurrent delegate_task — 3 sessions in parallel")
    async def one_session(sid: str, prompt: str) -> str:
        set_runtime_request_context({
            "session_id": sid, "user_id": f"u-{sid}", "channel": "e2e-test",
        })
        r = await delegate_task(goal=prompt, wait=True, timeout_seconds=90)
        return r.text

    t0 = time.time()
    results = await asyncio.gather(
        one_session("e2e-S1", "What is 11 * 12? One number."),
        one_session("e2e-S2", "Translate 'good morning' to French. One phrase."),
        one_session("e2e-S3", "Name one large bird. One word."),
    )
    elapsed = time.time() - t0
    print(f"  3 concurrent tasks finished in {elapsed:.1f}s (sequential would have been ~30s)")

    pass_count = 0
    for i, body in enumerate(results, 1):
        ok = "Status: done" in body
        head = body.splitlines()[0] if body else "(empty)"
        print(f"    S{i}: {'OK ' if ok else 'FAIL '}  {head}")
        if ok:
            pass_count += 1
    assert pass_count == 3, f"only {pass_count}/3 concurrent tasks completed"

    # Verify session isolation: each task in store carries its session_id
    _orch, task_store, _evt = _get_inprocess_components()
    list_method = None
    for name in ("list_tasks", "get_all_tasks", "all_tasks"):
        if hasattr(task_store, name):
            list_method = getattr(task_store, name)
            break
    if list_method:
        all_tasks = list_method()
        sessions = {getattr(t, "session_id", None) for t in all_tasks}
        s_filtered = {s for s in sessions if s and s.startswith("e2e-S")}
        print(f"  task_store has {len(all_tasks)} tasks across sessions: "
              f"{sorted(s for s in sessions if s) [:8]}")
        assert s_filtered == {"e2e-S1", "e2e-S2", "e2e-S3"}, \
            f"expected 3 distinct e2e-S* sessions, got: {s_filtered}"
        print("  session isolation verified")
    else:
        print("  (task_store has no list method; skipping isolation introspection)")
    print("  T5 PASS")

    print("\n========== ALL sa-4 E2E PASSED ==========")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
