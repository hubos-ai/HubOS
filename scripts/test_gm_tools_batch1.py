# -*- coding: utf-8 -*-
"""Stage B step 2c (batch 1): e2e tests for 3 new GM tools.

Covers:
  spawn_subagents       — fan-out / parallel / partial failure / no-runner
  recall_long_term      — empty store / message hits / session hits / no match
  recall_session        — found / truncation / not-found / user-mismatch

The host environment lacks ``agentscope`` and ``agentscope_runtime`` here, so
we stub them in ``sys.modules`` BEFORE importing the tool modules. The tools
themselves only use ``ToolResponse`` + ``TextBlock`` for return shape.

Memory state is fully isolated under ``HUBOS_MEMORY_ROOT=<tmpdir>``.

Run: python3 scripts/test_gm_tools_batch1.py
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
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

# ---------------------------------------------------------------------------
# Stub agentscope.* modules  (must be installed BEFORE importing the tools).
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

# httpx is optional — runtime_delegate imports it but we won't exercise it.
try:
    import httpx  # noqa: F401
except ImportError:
    sys.modules["httpx"] = types.ModuleType("httpx")  # type: ignore[assignment]
    sys.modules["httpx"].AsyncClient = object  # type: ignore[attr-defined]
    sys.modules["httpx"].RequestError = type("RequestError", (Exception,), {})  # type: ignore[attr-defined]

# Memory sandbox MUST be set before LocalMemoryStore is constructed.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="hubos_memtest_"))
os.environ["HUBOS_MEMORY_ROOT"] = str(_TMP_ROOT)

sys.path.insert(0, str(SRC))


def _load_module(name: str, rel_path: str):
    """Load a single source file under src/ as a top-level module without
    importing its parent package (which would pull heavy deps)."""
    full = SRC / rel_path
    spec = importlib.util.spec_from_file_location(name, full)
    assert spec and spec.loader, name
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Tools depend on runtime_delegate._current_runtime_ctx — stub that helper too.
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
# Also shim the parent packages so `from .runtime_delegate import _current_runtime_ctx`
# works as a relative import.
# Install *lightweight* stand-ins for the package spine so that the
# relative imports in the tool modules resolve without dragging the
# real ``hubos/__init__.py`` in (which configures logging, loads
# agentscope plugins, etc. and won't run cleanly under the test stubs).
# Crucially, the fake modules point ``__path__`` at the real on-disk
# directory so genuine submodule imports such as
# ``from hubos.core.workers...`` still resolve via the normal loader.
for pkg, rel in (
    ("hubos", "hubos"),
    ("hubos.agents", "hubos/agents"),
    ("hubos.agents.tools", "hubos/agents/tools"),
):
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(SRC / rel)]  # type: ignore[attr-defined]
        sys.modules[pkg] = m

# Now load the two tool modules. They are written as relative-import children
# of hubos.agents.tools, but using importlib with that fully-qualified name
# resolves the relative import correctly.
sys.modules["hubos.agents.tools.runtime_delegate"] = _rd_stub
agent_workforce = _load_module(
    "hubos.agents.tools.agent_workforce",
    "hubos/agents/tools/agent_workforce.py",
)
memory_recall = _load_module(
    "hubos.agents.tools.memory_recall",
    "hubos/agents/tools/memory_recall.py",
)

from hubos.core.workers.registry import (
    set_host_agent_runner,
    clear_host_agent_runner,
)  # noqa: E402
from hubos.core.memory import LocalMemoryStore  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(resp) -> str:
    return resp.text()


def _json_payload(resp):
    raw = _text(resp)
    try:
        return json.loads(raw)
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

    # ============ Group A: spawn_subagents ============

    # A1: no runner registered
    clear_host_agent_runner()
    resp = await agent_workforce.spawn_subagents(
        [{"agent_id": "a", "prompt": "p"}],
    )
    report(
        "A1 spawn_subagents fails cleanly when no runner registered",
        "No HostAgentRunner registered" in _text(resp),
        _text(resp)[:200],
    )

    # A2: empty assignments
    resp = await agent_workforce.spawn_subagents([])
    report(
        "A2 spawn_subagents rejects empty assignments",
        "non-empty list" in _text(resp),
        _text(resp)[:200],
    )

    # A3: missing agent_id / prompt
    async def _runner(agent_id, prompt, ctx):
        return f"{agent_id}: {prompt}"

    set_host_agent_runner(_runner)
    resp = await agent_workforce.spawn_subagents([{"agent_id": "a"}])
    report(
        "A3 spawn_subagents rejects missing prompt",
        "needs both" in _text(resp),
        _text(resp)[:200],
    )

    # A4: happy path — 3 agents, 3 results, all success
    _set_ctx({"session_id": "S1", "user_id": "U1", "channel": "web"})
    resp = await agent_workforce.spawn_subagents(
        [
            {
                "agent_id": "research",
                "prompt": "find market size",
                "label": "research",
            },
            {
                "agent_id": "writer",
                "prompt": "draft summary",
                "label": "writer",
            },
            {"agent_id": "qa", "prompt": "check facts", "label": "qa"},
        ],
    )
    payload = _json_payload(resp)
    a4_ok = (
        payload is not None
        and payload["spawned"] == 3
        and payload["succeeded"] == 3
        and payload["failed"] == 0
        and len(payload["results"]) == 3
        and all(r["success"] for r in payload["results"])
        and {r["label"] for r in payload["results"]}
        == {"research", "writer", "qa"}
    )
    report(
        "A4 spawn_subagents 3-way fan-out all success",
        a4_ok,
        json.dumps(payload)[:300],
    )

    # A5: parallelism — 3 slow runners run concurrently, total << sum
    async def _slow_runner(agent_id, prompt, ctx):
        await asyncio.sleep(0.5)
        return f"{agent_id}: done"

    set_host_agent_runner(_slow_runner)
    start = time.time()
    resp = await agent_workforce.spawn_subagents(
        [
            {"agent_id": "a", "prompt": "x", "label": "L1"},
            {"agent_id": "b", "prompt": "x", "label": "L2"},
            {"agent_id": "c", "prompt": "x", "label": "L3"},
        ],
        max_concurrency=4,
    )
    elapsed = time.time() - start
    payload = _json_payload(resp)
    # Sequential would be ~1.5s; parallel should be ~0.5-0.8s.
    a5_ok = payload is not None and payload["succeeded"] == 3 and elapsed < 1.0
    report(
        f"A5 spawn_subagents parallelism (3*0.5s in {elapsed:.2f}s)",
        a5_ok,
        f"elapsed={elapsed:.2f}s payload={json.dumps(payload)[:150]}",
    )

    # A6: partial failure — one runner raises
    async def _flaky_runner(agent_id, prompt, ctx):
        if agent_id == "broken":
            raise RuntimeError("offline")
        return f"{agent_id}: ok"

    set_host_agent_runner(_flaky_runner)
    resp = await agent_workforce.spawn_subagents(
        [
            {"agent_id": "good", "prompt": "x", "label": "good"},
            {"agent_id": "broken", "prompt": "x", "label": "broken"},
            {"agent_id": "good2", "prompt": "x", "label": "good2"},
        ],
    )
    payload = _json_payload(resp)
    a6_ok = (
        payload is not None
        and payload["succeeded"] == 2
        and payload["failed"] == 1
        and any(
            (not r["success"])
            and r["label"] == "broken"
            and r["error"]
            and "offline" in r["error"]
            for r in payload["results"]
        )
    )
    report(
        "A6 spawn_subagents tolerates partial failure",
        a6_ok,
        json.dumps(payload)[:300],
    )

    clear_host_agent_runner()
    _set_ctx(None)

    # ============ Group B: recall_long_term ============

    # Reset memory sandbox for deterministic state
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    # Force store singleton to re-init under fresh root
    memory_recall._store_singleton = None

    # B1: empty store
    resp = await memory_recall.recall_long_term("anything")
    b1_ok = "no matches" in _text(resp)
    report(
        "B1 recall_long_term: empty store -> friendly miss",
        b1_ok,
        _text(resp)[:200],
    )

    # Seed: two sessions, one matches by title, one matches by message body.
    store = LocalMemoryStore()
    now = datetime.now(timezone.utc).isoformat()
    store.create_session(
        "sess-001",
        {
            "session_id": "sess-001",
            "title": "Q3 marketing budget review",
            "started_at": now,
            "agent_id": "finance",
            "channel": "web",
            "user_id": "U1",
            "tags": ["budget", "Q3"],
        },
    )
    store.append_message(
        "sess-001",
        {
            "role": "user",
            "content": "what was our forecast?",
            "timestamp": now,
        },
    )
    store.append_message(
        "sess-001",
        {
            "role": "assistant",
            "content": "Forecast was 1.2M.",
            "timestamp": now,
        },
    )

    store.create_session(
        "sess-002",
        {
            "session_id": "sess-002",
            "title": "Travel plan",
            "started_at": now,
            "agent_id": "general",
            "channel": "web",
            "user_id": "U1",
            "tags": ["travel"],
        },
    )
    store.append_message(
        "sess-002",
        {
            "role": "assistant",
            "content": "Hotel booked: Q3 marketing event in Berlin.",
            "timestamp": now,
        },
    )

    store.create_session(
        "sess-003",
        {
            "session_id": "sess-003",
            "title": "Other user's stuff",
            "started_at": now,
            "agent_id": "general",
            "channel": "web",
            "user_id": "U_OTHER",
            "tags": ["irrelevant"],
        },
    )
    store.append_message(
        "sess-003",
        {
            "role": "user",
            "content": "completely different thing",
            "timestamp": now,
        },
    )

    # B2: title-only match
    _set_ctx({"session_id": "S", "user_id": "U1", "channel": "web"})
    resp = await memory_recall.recall_long_term(
        "budget",
        include_messages=False,
    )
    payload = _json_payload(resp)
    b2_ok = (
        payload is not None
        and any(h["session_id"] == "sess-001" for h in payload["session_hits"])
        and len(payload["message_hits"]) == 0
    )
    report(
        "B2 recall_long_term: title match (no msg search)",
        b2_ok,
        json.dumps(payload)[:300] if payload else _text(resp)[:200],
    )

    # B3: message-body match
    resp = await memory_recall.recall_long_term("Berlin")
    payload = _json_payload(resp)
    b3_ok = payload is not None and any(
        m.get("session_id") == "sess-002"
        and "Berlin" in (m.get("snippet") or "")
        for m in payload["message_hits"]
    )
    report(
        "B3 recall_long_term: message body match",
        b3_ok,
        json.dumps(payload)[:300] if payload else _text(resp)[:200],
    )

    # B4: user_id filter blocks other-user's session match
    resp = await memory_recall.recall_long_term(
        "different",
        include_messages=True,
    )
    payload = _json_payload(resp)
    b4_ok = payload is None or all(  # empty -> friendly text response
        s["session_id"] != "sess-003"
        for s in (payload.get("session_hits") or [])
    )
    report(
        "B4 recall_long_term: cross-user isolation",
        b4_ok,
        json.dumps(payload)[:300] if payload else _text(resp)[:200],
    )

    # B5: empty query rejected
    resp = await memory_recall.recall_long_term("")
    report(
        "B5 recall_long_term: empty query rejected",
        "cannot be empty" in _text(resp),
        _text(resp)[:200],
    )

    # ============ Group C: recall_session ============

    # C1: not found
    resp = await memory_recall.recall_session("nope-does-not-exist")
    payload = _json_payload(resp)
    c1_ok = payload is not None and payload["found"] is False
    report(
        "C1 recall_session: not found returns found=False",
        c1_ok,
        json.dumps(payload)[:200] if payload else _text(resp)[:200],
    )

    # C2: found, returns metadata + messages
    _set_ctx({"session_id": "S", "user_id": "U1", "channel": "web"})
    resp = await memory_recall.recall_session("sess-001", last_n=10)
    payload = _json_payload(resp)
    c2_ok = (
        payload is not None
        and payload["found"]
        and payload["metadata"]["session_id"] == "sess-001"
        and len(payload["messages"]) == 2
        and payload["truncated"] is False
        and payload["total_messages"] == 2
    )
    report(
        "C2 recall_session: full retrieval",
        c2_ok,
        json.dumps(payload)[:300],
    )

    # C3: truncation
    for i in range(50):
        store.append_message(
            "sess-002",
            {
                "role": "user" if i % 2 else "assistant",
                "content": f"msg{i}",
                "timestamp": now,
            },
        )
    resp = await memory_recall.recall_session("sess-002", last_n=5)
    payload = _json_payload(resp)
    c3_ok = (
        payload is not None
        and payload["truncated"] is True
        and len(payload["messages"]) == 5
        and payload["total_messages"] == 51  # 1 seed + 50
        and payload["messages"][-1]["content"] == "msg49"
    )
    report(
        "C3 recall_session: truncation keeps tail",
        c3_ok,
        json.dumps(payload)[:300],
    )

    # C4: cross-user denial
    _set_ctx({"session_id": "S", "user_id": "U1", "channel": "web"})
    resp = await memory_recall.recall_session("sess-003")  # owned by U_OTHER
    c4_ok = "access denied" in _text(resp)
    report("C4 recall_session: cross-user denied", c4_ok, _text(resp)[:200])

    # C5: empty session_id rejected
    resp = await memory_recall.recall_session("")
    report(
        "C5 recall_session: empty id rejected",
        "cannot be empty" in _text(resp),
        _text(resp)[:200],
    )

    _set_ctx(None)

    # ============ Group D: naming hygiene on the new tool files ============
    import re

    for fname in ("agent_workforce.py", "memory_recall.py"):
        src = (SRC / "hubos" / "agents" / "tools" / fname).read_text(
            encoding="utf-8",
        )
        bad = [
            t
            for t in ("openclaw", "hermes", "xclaw")
            if re.search(rf"\b{t}\b", src, re.IGNORECASE)
        ]
        report(f"D {fname} no foreign project names", not bad, f"hits: {bad}")

    print("")
    if failed:
        print(f"FAILED ({len(failed)}): {failed}")
        return 1
    print(
        f"ALL Stage B step 2c batch-1 checks passed ({len([1 for _ in []])}).",
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
