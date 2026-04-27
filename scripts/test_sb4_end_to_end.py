# -*- coding: utf-8 -*-
"""Stage B step 4: end-to-end closed-loop smoke.

Simulates the full GM flow, end-to-end, through the **real** code paths:

    scripted GM
      │
      ├─► coordinate_workflow(plan)         (real tool)
      │        │
      │        └─► HostAgentWorker × N      (real worker)
      │                └─► HostAgentRunner  (real adapter)
      │                      └─► MultiAgentManager.get_agent(agent_id)
      │                            └─► Workspace.runner.query_handler(...)
      │                                  (real async-generator protocol)
      │
      ├─► LocalMemoryStore.create_session + append_message × K   (real L4)
      │        (this is the GM's post-workflow archive step; auto-persist on
      │         workflow done is deferred to Stage C)
      │
      ├─► recall_long_term(query)           (real tool)
      └─► recall_session(session_id)        (real tool)

Only two things are faked:
  • AgentScope / agentscope_runtime modules — the test environment does not
    have them installed, and this project doesn't need the LLM runtime for
    Stage B. The stubs match the shape the adapters actually use.
  • The "GM reasoning" — a deterministic script, not an LLM. sb-4 verifies
    the **pipeline**, not the planner.

Parallelism claim: three leaf agents each sleep ``LEG_DELAY=0.4s``, the
summary step sleeps ``SUM_DELAY=0.25s``. Serial lower bound is 1.45s,
observed wall-time should be well below that.

Run: python3 scripts/test_sb4_end_to_end.py
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
sys.path.insert(0, str(SRC))


# ───────────────────────────────────────────────────────────────────────
# Stub agentscope / agentscope_runtime BEFORE importing any adapters.
# ───────────────────────────────────────────────────────────────────────
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
sys.modules["agentscope_runtime.engine.schemas.agent_schemas"] = _agent_schemas

try:
    import httpx  # noqa: F401
except ImportError:
    _httpx = types.ModuleType("httpx")
    _httpx.AsyncClient = object
    _httpx.RequestError = type("RequestError", (Exception,), {})
    sys.modules["httpx"] = _httpx


# ───────────────────────────────────────────────────────────────────────
# Memory sandbox — fresh tmp dir so L4 writes are isolated.
# ───────────────────────────────────────────────────────────────────────
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="hubos_sb4_"))
os.environ["HUBOS_MEMORY_ROOT"] = str(_TMP_ROOT)


# Stub runtime_delegate._current_runtime_ctx for the scripted GM context.
_rd_stub = types.ModuleType("hubos.agents.tools.runtime_delegate")
_rd_ctx: dict[str, str] = {}


def _set_ctx(ctx):
    _rd_ctx.clear()
    if ctx:
        _rd_ctx.update(ctx)


def _current_runtime_ctx():
    return dict(_rd_ctx)


_rd_stub.cancel_task = lambda *a, **k: None
_rd_stub.delegate_task = lambda *a, **k: None
_rd_stub.set_runtime_request_context = lambda *a, **k: None
_rd_stub.track_task = lambda *a, **k: None
_rd_stub._current_runtime_ctx = _current_runtime_ctx
sys.modules["hubos.agents.tools.runtime_delegate"] = _rd_stub


# Minimal package hierarchy so relative imports inside the loaded modules
# resolve without dragging in sibling heavy modules.
for pkg, subpath in (
    ("hubos", "hubos"),
    ("hubos.agents", "hubos/agents"),
    ("hubos.agents.tools", "hubos/agents/tools"),
    ("hubos.integrations", "hubos/integrations"),
):
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(SRC / subpath)]
        sys.modules[pkg] = m


def _load(name: str, rel_path: str):
    full = SRC / rel_path
    spec = importlib.util.spec_from_file_location(name, full)
    assert spec and spec.loader, name
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load the three tool modules we'll exercise + host adapter.
agent_workforce = _load(
    "hubos.agents.tools.agent_workforce",
    "hubos/agents/tools/agent_workforce.py",
)
memory_recall = _load(
    "hubos.agents.tools.memory_recall",
    "hubos/agents/tools/memory_recall.py",
)
host_adapter = _load(
    "hubos.integrations.host_agent_runner",
    "hubos/integrations/host_agent_runner.py",
)

from hubos.core.workers import (  # noqa: E402
    clear_host_agent_runner,
    get_host_agent_runner,
    set_host_agent_runner,
)
from hubos.core.memory.local_store import LocalMemoryStore  # noqa: E402


# ───────────────────────────────────────────────────────────────────────
# Fake MultiAgentManager — three topical agents, each with its own reply.
# ───────────────────────────────────────────────────────────────────────
LEG_DELAY = 0.4
SUM_DELAY = 0.25

_AGENT_REPLIES = {
    "trend_analyst": (
        "[trend] In 2026 multi-agent orchestration is mainstream; "
        "tool-augmented LLMs dominate."
    ),
    "safety_analyst": (
        "[safety] Key risks: prompt injection, cross-session leakage, "
        "unbounded sub-agent spawning."
    ),
    "openness_analyst": (
        "[open] Open-weight mid-size models (30B-70B) are now competitive "
        "with late-2024 closed flagships."
    ),
}


class _FakeRunner:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    async def query_handler(self, msgs, request):  # noqa: ARG002
        if self.agent_id == "summary_agent":
            await asyncio.sleep(SUM_DELAY)
            user_prompt = ""
            for m in msgs:
                if getattr(m, "role", None) == "user":
                    for b in getattr(m, "content", []) or []:
                        if getattr(b, "type", None) == "text":
                            user_prompt = getattr(b, "text", "") or user_prompt
            reply = (
                "[summary] Synthesis of three upstream agents. "
                "Grounding evidence from prompt length="
                f"{len(user_prompt)}."
            )
        else:
            await asyncio.sleep(LEG_DELAY)
            reply = _AGENT_REPLIES[self.agent_id]
        yield (
            types.SimpleNamespace(
                name=self.agent_id,
                role="assistant",
                content=[_StubTextBlock("text", reply)],
            ),
            True,
        )


class _FakeWorkspace:
    def __init__(self, agent_id: str) -> None:
        self.runner = _FakeRunner(agent_id)
        self.session_id = f"ws-{agent_id}"
        self.user_id = "GM"


class _FakeManager:
    def __init__(self) -> None:
        self._map = {
            aid: _FakeWorkspace(aid)
            for aid in (
                "trend_analyst",
                "safety_analyst",
                "openness_analyst",
                "summary_agent",
            )
        }

    async def get_agent(self, agent_id: str):
        return self._map[agent_id]


# ───────────────────────────────────────────────────────────────────────
# Test runner
# ───────────────────────────────────────────────────────────────────────
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAIL
    mark = "OK" if cond else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL += 1


async def main() -> int:
    # --- Wire host runner (same as _app.py at startup) ---
    clear_host_agent_runner()
    mgr = _FakeManager()
    set_host_agent_runner(host_adapter.build_host_agent_runner(mgr.get_agent))
    check("HostAgentRunner wired", get_host_agent_runner() is not None)

    # --- Scripted GM context (user U1, session sess-E2E) ---
    _set_ctx({"user_id": "U1", "session_id": "sess-E2E", "channel": "web"})

    # =========================================================
    # T1. coordinate_workflow: diamond DAG, 3 parallel + 1 summary.
    # =========================================================
    print("\n[T1] coordinate_workflow — 3 parallel analysts + 1 summary")
    plan = [
        {
            "id": "trend",
            "agent_id": "trend_analyst",
            "prompt": "Summarise 2026 AI trends in one line.",
        },
        {
            "id": "safety",
            "agent_id": "safety_analyst",
            "prompt": "List top safety risks for multi-agent systems.",
        },
        {
            "id": "openness",
            "agent_id": "openness_analyst",
            "prompt": "State of open-weight models vs closed flagships.",
        },
        {
            "id": "sumup",
            "agent_id": "summary_agent",
            "prompt": (
                "Synthesise these three reports:\n"
                "trend: {{step_trend.result}}\n"
                "safety: {{step_safety.result}}\n"
                "openness: {{step_openness.result}}"
            ),
            "depends_on": ["trend", "safety", "openness"],
        },
    ]

    t0 = time.perf_counter()
    resp = await agent_workforce.coordinate_workflow(
        steps=plan,
        title="AI landscape 2026",
        summary_step_id="sumup",
        wait=True,
        timeout_seconds=10,
    )
    elapsed = time.perf_counter() - t0

    payload = json.loads(resp.content[0].text)
    check(
        "workflow done",
        payload.get("status") == "done",
        detail=f"status={payload.get('status')!r}",
    )
    check(
        "has workflow_id with wf- prefix",
        isinstance(payload.get("workflow_id"), str)
        and payload["workflow_id"].startswith("wf-"),
        detail=payload.get("workflow_id", "<missing>"),
    )
    step_by_id = {s["id"]: s for s in payload.get("steps", [])}
    for sid in ("trend", "safety", "openness", "sumup"):
        check(
            f"step {sid} done",
            step_by_id.get(sid, {}).get("status") == "done",
            detail=str(step_by_id.get(sid, {}).get("status")),
        )

    summary_text = payload.get("final_response") or ""
    check(
        "final_response non-empty",
        isinstance(summary_text, str) and len(summary_text) > 0,
    )
    check(
        "summary step received expanded prompt (non-zero length grounded)",
        "Grounding evidence from prompt length=" in summary_text
        and "length=0" not in summary_text,
        detail=summary_text[:80],
    )

    # Parallelism: serial lower bound = 3*LEG_DELAY + SUM_DELAY = 1.45s.
    # Parallel lower bound = 1*LEG_DELAY + SUM_DELAY = 0.65s.
    check(
        "3 leaf steps actually ran in parallel",
        elapsed < 1.3,
        detail=f"elapsed={elapsed:.2f}s (serial would be ~1.45s)",
    )
    # Sanity: must not be instant (i.e. real delay was observed).
    check(
        "workflow did real work (elapsed >= 0.5s)",
        elapsed >= 0.5,
        detail=f"elapsed={elapsed:.2f}s",
    )

    # Capture the content the GM would have archived.
    archived_msgs = [
        ("user", "Give me a landscape report on AI in 2026."),
        (
            "assistant",
            "Dispatching coordinate_workflow with 4 steps "
            "(3 parallel analysts + 1 summary).",
        ),
        ("assistant", f"[trend] {step_by_id['trend']['result']}"),
        ("assistant", f"[safety] {step_by_id['safety']['result']}"),
        ("assistant", f"[openness] {step_by_id['openness']['result']}"),
        ("assistant", f"[summary] {summary_text}"),
    ]

    # =========================================================
    # T2. GM archive step — write the session to L4.
    # =========================================================
    print("\n[T2] GM archive — write workflow transcript to L4")
    store = LocalMemoryStore()
    now_iso = datetime.now(timezone.utc).isoformat()
    session_id = "sess-E2E"
    store.create_session(
        session_id,
        {
            "session_id": session_id,
            "title": "AI landscape 2026 workflow run",
            "started_at": now_iso,
            "agent_id": "gm",
            "channel": "web",
            "user_id": "U1",
            "tags": ["workflow", "ai-landscape"],
            "workflow_id": payload["workflow_id"],
        },
    )
    for role, content in archived_msgs:
        store.append_message(
            session_id,
            {"role": role, "content": content, "timestamp": now_iso},
        )

    sessions_dir = (
        Path(os.environ["HUBOS_MEMORY_ROOT"]) / "sessions" / session_id
    )
    check("session dir exists", sessions_dir.exists())
    messages_jsonl = sessions_dir / "messages.jsonl"
    check("messages.jsonl exists", messages_jsonl.exists())
    line_count = sum(
        1 for _ in messages_jsonl.read_text(encoding="utf-8").splitlines() if _
    )
    check(
        f"messages.jsonl has {len(archived_msgs)} lines",
        line_count == len(archived_msgs),
        detail=f"got {line_count}",
    )

    # =========================================================
    # T3. recall_long_term finds the archived session.
    # =========================================================
    print("\n[T3] recall_long_term — fuzzy search over L4")
    r1 = await memory_recall.recall_long_term(
        query="AI landscape 2026",
        top_k=5,
        include_messages=True,
    )
    p1 = json.loads(r1.content[0].text)
    session_hits = p1.get("session_hits") or []
    ids_found = [s.get("session_id") for s in session_hits]
    check(
        "recall_long_term returns the archived session by title",
        session_id in ids_found,
        detail=f"ids={ids_found}",
    )
    check(
        "recall_long_term total > 0",
        (p1.get("total", 0) or 0) > 0,
        detail=str(p1.get("total")),
    )

    # Search by a keyword that should hit a message body (trend content).
    r1b = await memory_recall.recall_long_term(
        query="mainstream",
        top_k=5,
        include_messages=True,
    )
    p1b = json.loads(r1b.content[0].text)
    message_hits = p1b.get("message_hits") or []
    check(
        "recall_long_term finds content-level match ('mainstream')",
        any(
            "mainstream" in (m.get("content") or "").lower()
            for m in message_hits
        )
        or any(session_id == m.get("session_id") for m in message_hits),
        detail=f"message_hits={len(message_hits)}",
    )

    # =========================================================
    # T4. recall_session loads the full session back.
    # =========================================================
    print("\n[T4] recall_session — full transcript replay")
    r2 = await memory_recall.recall_session(
        session_id=session_id,
        last_n=100,
    )
    p2 = json.loads(r2.content[0].text)
    meta = p2.get("metadata") or {}
    msgs = p2.get("messages") or []
    check(
        "recall_session found=True",
        p2.get("found") is True,
    )
    check(
        "recall_session returns correct session_id",
        meta.get("session_id") == session_id,
        detail=str(meta.get("session_id")),
    )
    check(
        f"recall_session returns all {len(archived_msgs)} messages",
        len(msgs) == len(archived_msgs),
        detail=f"got {len(msgs)}",
    )
    last_content = msgs[-1].get("content") if msgs else ""
    check(
        "last message is the summary",
        isinstance(last_content, str) and "[summary]" in last_content,
        detail=last_content[:60]
        if isinstance(last_content, str)
        else "<non-str>",
    )

    # =========================================================
    # T5. Cross-user access control.
    # =========================================================
    print("\n[T5] cross-user denial — U2 must not read U1's session")
    _set_ctx({"user_id": "U2", "session_id": "sess-other", "channel": "web"})
    r3 = await memory_recall.recall_session(
        session_id=session_id,
        last_n=10,
    )
    raw = r3.content[0].text
    # recall_session's error path returns plain text (no JSON wrapper).
    denied = "denied" in raw.lower() or "different user" in raw.lower()
    if not denied:
        try:
            p3 = json.loads(raw)
            denied = p3.get("found") is False or not p3.get("messages")
        except Exception:
            denied = False
    check(
        "U2 recall_session on U1 session is refused",
        denied,
        detail=raw[:120],
    )
    _set_ctx({"user_id": "U1", "session_id": session_id, "channel": "web"})

    # =========================================================
    # T6. Naming hygiene on production modules touched by the pipeline.
    # =========================================================
    print("\n[T6] naming hygiene — production modules used by this e2e")
    forbidden = [chr(111) + "penclaw", "x" + "claw", "her" + "mes"]
    prod_files = [
        SRC / "hubos/agents/tools/agent_workforce.py",
        SRC / "hubos/agents/tools/memory_recall.py",
        SRC / "hubos/agents/tools/__init__.py",
        SRC / "hubos/integrations/host_agent_runner.py",
        SRC / "hubos/core/workers/registry.py",
        SRC / "hubos/core/workers/providers/host_agent.py",
    ]
    for f in prod_files:
        blob = f.read_text(encoding="utf-8").lower()
        for bad in forbidden:
            check(
                f"{f.name} clean of {bad!r}",
                bad not in blob,
            )

    # =========================================================
    # Teardown
    # =========================================================
    clear_host_agent_runner()
    return FAIL


if __name__ == "__main__":
    rc = asyncio.run(main())
    try:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    except Exception:
        pass
    print("\n" + "=" * 64)
    print(f"Result: {'ALL PASSED' if rc == 0 else f'{rc} FAILED'}")
    print("=" * 64)
    sys.exit(0 if rc == 0 else 1)
