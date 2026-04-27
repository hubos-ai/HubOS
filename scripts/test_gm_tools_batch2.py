# -*- coding: utf-8 -*-
"""Stage B step 2c (batch 2): e2e tests for 3 workflow-DAG tools.

Covers:
  coordinate_workflow  — validation, linear DAG, parallel diamond,
                         prompt templating, upstream-failure cascade,
                         wait=False detach, timeout
  track_workflow       — running snapshot, follow-to-done, not-found,
                         cross-user denial
  cancel_workflow      — signals cancel, steps marked cancelled,
                         already-terminal no-op, not-found

Stubs the same way as batch 1 (no real agentscope runtime needed).

Run: python3 scripts/test_gm_tools_batch2.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

# ---------------------------------------------------------------------------
# agentscope.* stubs
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

    def text(self) -> str:
        out = []
        for b in self.content:
            t = getattr(b, "text", None) or (
                b.get("text") if isinstance(b, dict) else None
            )
            if isinstance(t, str):
                out.append(t)
        return "\n".join(out)


_agentscope = types.ModuleType("agentscope")
_agentscope_message = types.ModuleType("agentscope.message")
_agentscope_message.TextBlock = _StubTextBlock
_agentscope_tool = types.ModuleType("agentscope.tool")
_agentscope_tool.ToolResponse = _StubToolResponse
_agentscope.message = _agentscope_message  # type: ignore[attr-defined]
_agentscope.tool = _agentscope_tool  # type: ignore[attr-defined]

sys.modules["agentscope"] = _agentscope
sys.modules["agentscope.message"] = _agentscope_message
sys.modules["agentscope.tool"] = _agentscope_tool

try:
    import httpx  # noqa: F401
except ImportError:
    _httpx = types.ModuleType("httpx")
    _httpx.AsyncClient = object
    _httpx.RequestError = type("RequestError", (Exception,), {})
    sys.modules["httpx"] = _httpx

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="hubos_wftest_"))
os.environ["HUBOS_MEMORY_ROOT"] = str(_TMP_ROOT)

sys.path.insert(0, str(SRC))

# runtime_delegate shim (same as batch 1)
_rd_stub = types.ModuleType("hubos.agents.tools.runtime_delegate")
_rd_ctx: dict[str, str] = {}


def _set_ctx(ctx):
    _rd_ctx.clear()
    if ctx:
        _rd_ctx.update(ctx)


def _current_runtime_ctx():
    return dict(_rd_ctx)


_rd_stub._current_runtime_ctx = _current_runtime_ctx
sys.modules["hubos.agents.tools.runtime_delegate"] = _rd_stub

for pkg, rel in (
    ("hubos", "hubos"),
    ("hubos.agents", "hubos/agents"),
    ("hubos.agents.tools", "hubos/agents/tools"),
):
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(SRC / rel)]  # type: ignore[attr-defined]
        sys.modules[pkg] = m

sys.modules["hubos.agents.tools.runtime_delegate"] = _rd_stub


def _load_module(name: str, rel_path: str):
    full = SRC / rel_path
    spec = importlib.util.spec_from_file_location(name, full)
    assert spec and spec.loader, name
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


agent_workforce = _load_module(
    "hubos.agents.tools.agent_workforce",
    "hubos/agents/tools/agent_workforce.py",
)

from hubos.core.workers.registry import (  # noqa: E402
    clear_host_agent_runner,
    set_host_agent_runner,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _text(resp) -> str:
    return resp.text()


def _payload(resp):
    try:
        return json.loads(_text(resp))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def main() -> int:
    failed: list[str] = []

    def report(name, ok, detail=""):
        if ok:
            print(f"[PASS] {name}")
        else:
            print(f"[FAIL] {name}: {detail}")
            failed.append(name)

    # ============ coordinate_workflow: validation ============

    # V1 no runner registered
    clear_host_agent_runner()
    resp = await agent_workforce.coordinate_workflow(
        [{"id": "a", "agent_id": "x", "prompt": "p"}],
    )
    report(
        "V1 no runner -> error",
        "No HostAgentRunner" in _text(resp),
        _text(resp)[:200],
    )

    async def _runner(agent_id, prompt, ctx):
        # echo back the prompt so templating is observable
        return f"[{agent_id}] {prompt}"

    set_host_agent_runner(_runner)

    # V2 empty plan
    resp = await agent_workforce.coordinate_workflow([])
    report(
        "V2 empty steps rejected",
        "non-empty list" in _text(resp),
        _text(resp)[:200],
    )

    # V3 missing fields
    resp = await agent_workforce.coordinate_workflow([{"id": "a"}])
    report(
        "V3 missing fields rejected",
        "non-empty" in _text(resp),
        _text(resp)[:200],
    )

    # V4 duplicate id
    resp = await agent_workforce.coordinate_workflow(
        [
            {"id": "a", "agent_id": "x", "prompt": "p"},
            {"id": "a", "agent_id": "x", "prompt": "q"},
        ],
    )
    report(
        "V4 duplicate ids rejected",
        "duplicate" in _text(resp),
        _text(resp)[:200],
    )

    # V5 missing dep
    resp = await agent_workforce.coordinate_workflow(
        [
            {
                "id": "a",
                "agent_id": "x",
                "prompt": "p",
                "depends_on": ["ghost"],
            },
        ],
    )
    report(
        "V5 missing dep rejected",
        "missing step" in _text(resp),
        _text(resp)[:200],
    )

    # V6 cycle
    resp = await agent_workforce.coordinate_workflow(
        [
            {"id": "a", "agent_id": "x", "prompt": "p", "depends_on": ["b"]},
            {"id": "b", "agent_id": "x", "prompt": "q", "depends_on": ["a"]},
        ],
    )
    report("V6 cycle rejected", "cycle" in _text(resp), _text(resp)[:200])

    # V7 summary_step_id must exist
    resp = await agent_workforce.coordinate_workflow(
        [{"id": "a", "agent_id": "x", "prompt": "p"}],
        summary_step_id="ghost",
    )
    report(
        "V7 bad summary_step_id rejected",
        "summary_step_id" in _text(resp),
        _text(resp)[:200],
    )

    # V8 bad id chars
    resp = await agent_workforce.coordinate_workflow(
        [{"id": "bad id!", "agent_id": "x", "prompt": "p"}],
    )
    report(
        "V8 bad id chars rejected",
        "must match" in _text(resp),
        _text(resp)[:200],
    )

    # ============ coordinate_workflow: execution ============

    _set_ctx({"session_id": "S1", "user_id": "U1", "channel": "web"})

    # E1 linear chain A -> B -> C with prompt templating
    resp = await agent_workforce.coordinate_workflow(
        steps=[
            {"id": "A", "agent_id": "r", "prompt": "facts"},
            {
                "id": "B",
                "agent_id": "w",
                "prompt": "based on: {{step_A.result}}",
                "depends_on": ["A"],
            },
            {
                "id": "C",
                "agent_id": "q",
                "prompt": "polish: {{step_B.result}}",
                "depends_on": ["B"],
            },
        ],
        summary_step_id="C",
        title="linear",
    )
    p = _payload(resp)
    e1_ok = (
        p is not None
        and p["status"] == "done"
        and p["workflow_id"].startswith("wf-")
        and all(s["status"] == "done" for s in p["steps"])
        and p["final_response"] is not None
        and "[q] polish: [w] based on: [r] facts" in p["final_response"]
    )
    report(
        "E1 linear A->B->C with templating",
        e1_ok,
        json.dumps(p)[:400] if p else _text(resp)[:300],
    )

    # E2 diamond: A -> (B, C) parallel -> D, with 0.3s slow runner
    slow_hits: list[str] = []
    gate = asyncio.Event()

    async def _slow_runner(agent_id, prompt, ctx):
        slow_hits.append(ctx.get("step_id", "?"))
        await asyncio.sleep(0.3)
        return f"[{agent_id}] {prompt}"

    set_host_agent_runner(_slow_runner)
    start = time.time()
    resp = await agent_workforce.coordinate_workflow(
        steps=[
            {"id": "A", "agent_id": "r", "prompt": "plan"},
            {
                "id": "B",
                "agent_id": "w1",
                "prompt": "draft from: {{step_A.result}}",
                "depends_on": ["A"],
            },
            {
                "id": "C",
                "agent_id": "w2",
                "prompt": "qa from: {{step_A.result}}",
                "depends_on": ["A"],
            },
            {
                "id": "D",
                "agent_id": "s",
                "prompt": "merge: {{step_B.result}} | {{step_C.result}}",
                "depends_on": ["B", "C"],
            },
        ],
        summary_step_id="D",
        max_concurrency=4,
    )
    elapsed = time.time() - start
    p = _payload(resp)
    # Serial cost would be 4*0.3 = 1.2s. Parallel (B+C run together) ≈ 3*0.3 = 0.9s.
    e2_ok = (
        p is not None
        and p["status"] == "done"
        and len(p["steps"]) == 4
        and all(s["status"] == "done" for s in p["steps"])
        and p["final_response"] is not None
        and "[r] plan" in p["final_response"]  # templated through B & C
        and 0.75 < elapsed < 1.15  # parallel in the middle
    )
    report(
        f"E2 diamond DAG parallel middle (elapsed {elapsed:.2f}s vs serial 1.2s)",
        e2_ok,
        f"elapsed={elapsed:.2f}s payload={json.dumps(p)[:300] if p else _text(resp)[:300]}",
    )

    # E3 upstream failure -> downstream skipped
    async def _flaky_runner(agent_id, prompt, ctx):
        if agent_id == "bomb":
            raise RuntimeError("boom")
        return f"[{agent_id}] {prompt}"

    set_host_agent_runner(_flaky_runner)
    resp = await agent_workforce.coordinate_workflow(
        steps=[
            {"id": "A", "agent_id": "ok", "prompt": "p"},
            {
                "id": "B",
                "agent_id": "bomb",
                "prompt": "q",
                "depends_on": ["A"],
            },
            {"id": "C", "agent_id": "ok2", "prompt": "r", "depends_on": ["B"]},
        ],
        summary_step_id="C",
    )
    p = _payload(resp)
    by_id = {s["id"]: s for s in (p["steps"] if p else [])}
    e3_ok = (
        p is not None
        and p["status"] == "failed"
        and p["final_response"] is None
        and by_id["A"]["status"] == "done"
        and by_id["B"]["status"] == "failed"
        and by_id["C"]["status"] == "skipped"
    )
    report(
        "E3 upstream fail -> downstream skipped",
        e3_ok,
        json.dumps(p)[:400] if p else _text(resp)[:300],
    )

    # E4 wait=False returns fast with 'running' + workflow_id
    set_host_agent_runner(_slow_runner)
    resp = await agent_workforce.coordinate_workflow(
        steps=[{"id": "A", "agent_id": "x", "prompt": "p"}],
        wait=False,
    )
    p = _payload(resp)
    e4_ok = (
        p is not None
        and p["status"] == "running"
        and p["workflow_id"].startswith("wf-")
    )
    wf_id_running = p["workflow_id"] if p else None
    report(
        "E4 wait=False detaches",
        e4_ok,
        json.dumps(p)[:200] if p else _text(resp)[:200],
    )

    # ============ track_workflow ============

    # T1 track running workflow (not follow): sees either running or done
    assert wf_id_running is not None
    resp = await agent_workforce.track_workflow(wf_id_running, follow=False)
    p = _payload(resp)
    t1_ok = (
        p is not None
        and p["workflow_id"] == wf_id_running
        and p["status"] in {"pending", "running", "done"}
    )
    report(
        "T1 track non-follow snapshot",
        t1_ok,
        json.dumps(p)[:200] if p else _text(resp)[:200],
    )

    # T2 track with follow -> should complete
    resp = await agent_workforce.track_workflow(
        wf_id_running,
        follow=True,
        timeout_seconds=5,
    )
    p = _payload(resp)
    t2_ok = p is not None and p["status"] == "done"
    report(
        "T2 track follow-to-done",
        t2_ok,
        json.dumps(p)[:200] if p else _text(resp)[:200],
    )

    # T3 track not-found
    resp = await agent_workforce.track_workflow("wf-nope")
    report(
        "T3 track not-found error",
        "not found" in _text(resp),
        _text(resp)[:200],
    )

    # T4 cross-user denial
    resp_make = await agent_workforce.coordinate_workflow(
        steps=[{"id": "A", "agent_id": "x", "prompt": "p"}],
        wait=True,
    )
    wf_u1 = _payload(resp_make)["workflow_id"]
    _set_ctx({"session_id": "S2", "user_id": "U_OTHER", "channel": "web"})
    resp = await agent_workforce.track_workflow(wf_u1)
    report(
        "T4 cross-user denial on track",
        "access denied" in _text(resp),
        _text(resp)[:200],
    )

    _set_ctx({"session_id": "S1", "user_id": "U1", "channel": "web"})

    # ============ cancel_workflow ============

    # C1 cancel a running workflow: long-running runner that waits on gate
    long_running_started = asyncio.Event()
    long_running_should_finish = asyncio.Event()

    async def _long_runner(agent_id, prompt, ctx):
        long_running_started.set()
        try:
            await asyncio.wait_for(
                long_running_should_finish.wait(),
                timeout=10,
            )
        except asyncio.TimeoutError:
            pass
        return f"[{agent_id}] {prompt}"

    set_host_agent_runner(_long_runner)
    resp = await agent_workforce.coordinate_workflow(
        steps=[
            {"id": "A", "agent_id": "a", "prompt": "p"},
            {"id": "B", "agent_id": "b", "prompt": "q", "depends_on": ["A"]},
        ],
        wait=False,
    )
    wf_cancel_id = _payload(resp)["workflow_id"]

    # Wait until A actually started
    await asyncio.wait_for(long_running_started.wait(), timeout=2)

    resp = await agent_workforce.cancel_workflow(wf_cancel_id)
    p = _payload(resp)
    c1_ok = (
        p is not None
        and p["status"] == "cancelled"
        and "cancel signalled" in p.get("note", "")
    )
    report(
        "C1 cancel running workflow -> cancelled",
        c1_ok,
        json.dumps(p)[:400] if p else _text(resp)[:300],
    )

    # Let any lingering task drain
    long_running_should_finish.set()

    # C2 cancel already-terminal workflow: no-op
    set_host_agent_runner(_runner)
    resp = await agent_workforce.coordinate_workflow(
        steps=[{"id": "A", "agent_id": "x", "prompt": "p"}],
        wait=True,
    )
    wf_done_id = _payload(resp)["workflow_id"]
    resp = await agent_workforce.cancel_workflow(wf_done_id)
    p = _payload(resp)
    c2_ok = (
        p is not None
        and p["status"] == "done"  # unchanged
        and "already terminal" in p.get("note", "")
    )
    report(
        "C2 cancel already-terminal -> no-op",
        c2_ok,
        json.dumps(p)[:300] if p else _text(resp)[:200],
    )

    # C3 cancel not-found
    resp = await agent_workforce.cancel_workflow("wf-nope")
    report(
        "C3 cancel not-found error",
        "not found" in _text(resp),
        _text(resp)[:200],
    )

    # C4 cancel empty id
    resp = await agent_workforce.cancel_workflow("")
    report(
        "C4 cancel empty id rejected",
        "required" in _text(resp),
        _text(resp)[:200],
    )

    clear_host_agent_runner()
    _set_ctx(None)

    # ============ Naming hygiene: new additions ============
    import re as _re

    src_text = (
        SRC / "hubos" / "agents" / "tools" / "agent_workforce.py"
    ).read_text(encoding="utf-8")
    bad = [
        t
        for t in ("openclaw", "hermes", "xclaw")
        if _re.search(rf"\b{t}\b", src_text, _re.IGNORECASE)
    ]
    report(
        "Hygiene agent_workforce.py no foreign project names",
        not bad,
        f"hits: {bad}",
    )

    print("")
    if failed:
        print(f"FAILED ({len(failed)}): {failed}")
        return 1
    print("ALL Stage B step 2c batch-2 checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
