# -*- coding: utf-8 -*-
"""Stage B step 2a: end-to-end test for the host-side HostAgentRunner adapter.

The host environment on this machine lacks ``agentscope_runtime`` (the host
runs under a different Python). We stub the two heavy modules in
``sys.modules`` so the adapter can be imported and exercised in isolation.

Checks:
  T1  happy path -> returns final assistant text
  T2  multiple yields -> picks LAST assistant message, not first
  T3  workspace.runner is None -> RuntimeError
  T4  runner.query_handler raises -> propagates as-is
  T5  cancellation propagates as CancelledError
  T6  Msg list + AgentRequest are actually passed through to runner
  T7  AgentRequest(input=, channel=) TypeError -> fallback construction
  T8  _extract_text: dict/list/object/empty content shapes
  T9  context default fallbacks (session_id / user_id / channel)
  T10 source file naming hygiene (no banned project names)

Run: python3 scripts/test_host_agent_runner.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

# ---------------------------------------------------------------------------
# Stub the two heavy dependencies BEFORE importing the adapter.
# ---------------------------------------------------------------------------


class _StubTextBlock:
    def __init__(
        self,
        type: str = "text",
        text: str = "",
    ) -> None:  # noqa: A002
        self.type = type
        self.text = text


class _StubMsg:
    def __init__(self, name: str, role: str, content) -> None:
        self.name = name
        self.role = role
        self.content = content

    def get_text_content(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = []
            for b in self.content:
                t = getattr(b, "text", None) or (
                    b.get("text") if isinstance(b, dict) else None
                )
                if isinstance(t, str):
                    parts.append(t)
            return "\n".join(parts)
        return ""


class _StubAgentRequest:
    def __init__(
        self,
        *,
        session_id,
        user_id,
        input=None,
        channel=None,
    ) -> None:  # noqa: A002
        self.session_id = session_id
        self.user_id = user_id
        self.input = input
        self.channel = channel


# Build module skeletons.
_agentscope = types.ModuleType("agentscope")
_agentscope_message = types.ModuleType("agentscope.message")
_agentscope_message.Msg = _StubMsg
_agentscope_message.TextBlock = _StubTextBlock
_agentscope.message = _agentscope_message  # type: ignore[attr-defined]

_agentscope_runtime = types.ModuleType("agentscope_runtime")
_arn_engine = types.ModuleType("agentscope_runtime.engine")
_arn_schemas = types.ModuleType("agentscope_runtime.engine.schemas")
_arn_agent_schemas = types.ModuleType(
    "agentscope_runtime.engine.schemas.agent_schemas",
)
_arn_agent_schemas.AgentRequest = _StubAgentRequest
_arn_schemas.agent_schemas = _arn_agent_schemas  # type: ignore[attr-defined]
_arn_engine.schemas = _arn_schemas  # type: ignore[attr-defined]
_agentscope_runtime.engine = _arn_engine  # type: ignore[attr-defined]

sys.modules["agentscope"] = _agentscope
sys.modules["agentscope.message"] = _agentscope_message
sys.modules["agentscope_runtime"] = _agentscope_runtime
sys.modules["agentscope_runtime.engine"] = _arn_engine
sys.modules["agentscope_runtime.engine.schemas"] = _arn_schemas
sys.modules[
    "agentscope_runtime.engine.schemas.agent_schemas"
] = _arn_agent_schemas

# Adapter file lives under src/hubos/integrations/. Importing the package
# would pull in hubos/__init__.py which has its own deps; load the module
# directly from path instead.
sys.path.insert(0, str(SRC))
import importlib.util  # noqa: E402

_adapter_path = SRC / "hubos" / "integrations" / "host_agent_runner.py"
_spec = importlib.util.spec_from_file_location(
    "host_agent_runner_under_test",
    _adapter_path,
)
assert _spec and _spec.loader
_adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapter)  # type: ignore[union-attr]

build_host_agent_runner = _adapter.build_host_agent_runner
_extract_text = _adapter._extract_text
DEFAULT_USER_ID = _adapter.DEFAULT_USER_ID
DEFAULT_CHANNEL = _adapter.DEFAULT_CHANNEL


# ---------------------------------------------------------------------------
# Stub Workspace + Runner.
# ---------------------------------------------------------------------------


class _StubRunner:
    def __init__(self, script):
        """script: list of (msg, last) or callable returning async-gen of those."""
        self.script = script
        self.captured_msgs = None
        self.captured_request = None

    async def query_handler(self, msgs, request=None):
        self.captured_msgs = msgs
        self.captured_request = request
        if callable(self.script):
            async for item in self.script():
                yield item
            return
        for item in self.script:
            yield item


class _StubWorkspace:
    def __init__(self, runner):
        self.runner = runner


def _make_provider(ws):
    async def _p(agent_id):
        return ws

    return _p


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

    # T1 happy path
    ws = _StubWorkspace(
        _StubRunner(
            [
                (
                    _StubMsg(
                        "Friday",
                        "assistant",
                        [_StubTextBlock("text", "hello back")],
                    ),
                    True,
                ),
            ],
        ),
    )
    runner = build_host_agent_runner(_make_provider(ws))
    out = await runner("gm", "say hi", {})
    report(
        "T1 happy path returns assistant text",
        out == "hello back",
        repr(out),
    )

    # T2 last assistant wins (not first)
    ws2 = _StubWorkspace(
        _StubRunner(
            [
                (_StubMsg("Friday", "assistant", "draft 1"), False),
                (_StubMsg("Friday", "assistant", "draft 2"), False),
                (_StubMsg("Friday", "assistant", "FINAL"), True),
            ],
        ),
    )
    out = await build_host_agent_runner(_make_provider(ws2))("gm", "x", {})
    report("T2 last assistant message wins", out == "FINAL", repr(out))

    # T3 missing runner
    ws3 = _StubWorkspace(None)
    try:
        await build_host_agent_runner(_make_provider(ws3))("gm", "x", {})
        report("T3 missing runner raises RuntimeError", False, "no exception")
    except RuntimeError as e:
        report(
            "T3 missing runner raises RuntimeError",
            "no runner" in str(e),
            str(e),
        )

    # T4 runner exception propagates raw
    class _BoomRunner:
        async def query_handler(self, msgs, request=None):
            raise ValueError("boom")
            yield  # noqa: unreachable - keeps it an async-gen for type purposes

    try:
        await build_host_agent_runner(
            _make_provider(_StubWorkspace(_BoomRunner())),
        )("gm", "x", {})
        report("T4 runner exception propagates", False, "no exception")
    except ValueError as e:
        report("T4 runner exception propagates", str(e) == "boom", str(e))
    except Exception as e:  # noqa: BLE001
        report(
            "T4 runner exception propagates",
            False,
            f"got {type(e).__name__}",
        )

    # T5 cancellation propagates
    started = asyncio.Event()
    cancelled_inside = asyncio.Event()

    class _SlowRunner:
        async def query_handler(self, msgs, request=None):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled_inside.set()
                raise
            yield (_StubMsg("Friday", "assistant", "never"), True)

    coro = build_host_agent_runner(
        _make_provider(_StubWorkspace(_SlowRunner())),
    )("gm", "x", {})
    task = asyncio.create_task(coro)
    await started.wait()
    task.cancel()
    propagated = False
    try:
        await task
    except asyncio.CancelledError:
        propagated = True
    report(
        "T5 cancellation propagates",
        propagated and cancelled_inside.is_set(),
        f"propagated={propagated} inside={cancelled_inside.is_set()}",
    )

    # T6 msgs + request properly passed
    captured_runner = _StubRunner(
        [
            (_StubMsg("Friday", "assistant", "ok"), True),
        ],
    )
    await build_host_agent_runner(
        _make_provider(_StubWorkspace(captured_runner)),
    )(
        "billing-bot",
        "refund order #42",
        {"session_id": "S-1", "user_id": "U-1", "channel": "web"},
    )
    msgs_ok = (
        isinstance(captured_runner.captured_msgs, list)
        and len(captured_runner.captured_msgs) == 1
        and isinstance(captured_runner.captured_msgs[0], _StubMsg)
        and captured_runner.captured_msgs[0].role == "user"
        and captured_runner.captured_msgs[0].content[0].text
        == "refund order #42"
    )
    req = captured_runner.captured_request
    req_ok = (
        isinstance(req, _StubAgentRequest)
        and req.session_id == "S-1"
        and req.user_id == "U-1"
        and req.channel == "web"
        and isinstance(req.input, list)
    )
    report(
        "T6 msgs passed correctly",
        msgs_ok,
        repr(captured_runner.captured_msgs),
    )
    report("T6 AgentRequest passed correctly", req_ok, repr(req))

    # T7 AgentRequest(input=...) TypeError -> fallback
    class _StrictAgentRequest:
        def __init__(
            self,
            *,
            session_id,
            user_id,
            input=None,
            channel=None,
        ):  # noqa: A002
            if input is not None or channel is not None:
                raise TypeError("only minimal fields supported")
            self.session_id = session_id
            self.user_id = user_id

    sys.modules[
        "agentscope_runtime.engine.schemas.agent_schemas"
    ].AgentRequest = _StrictAgentRequest
    try:
        captured = _StubRunner([(_StubMsg("Friday", "assistant", "ok"), True)])
        out = await build_host_agent_runner(
            _make_provider(_StubWorkspace(captured)),
        )("gm", "x", {"session_id": "S2"})
        ok = out == "ok" and isinstance(
            captured.captured_request,
            _StrictAgentRequest,
        )
        report(
            "T7 AgentRequest TypeError -> fallback works",
            ok,
            repr(captured.captured_request),
        )
    finally:
        sys.modules[
            "agentscope_runtime.engine.schemas.agent_schemas"
        ].AgentRequest = _StubAgentRequest

    # T8 _extract_text shape compatibility
    cases = [
        (
            "dict + list[dict]",
            {"content": [{"type": "text", "text": "hi"}]},
            "hi",
        ),
        ("dict + str", {"content": "hi"}, "hi"),
        (
            "obj + list[block]",
            _StubMsg("a", "assistant", [_StubTextBlock("text", "hi")]),
            "hi",
        ),
        # Multi-block + type filter only applies on the FALLBACK path
        # (msg has no get_text_content). Use a dict to trigger fallback.
        (
            "dict + multi-block, picks text-only",
            {
                "content": [
                    {"type": "image", "text": "ignored"},
                    {"type": "text", "text": "kept"},
                ],
            },
            "kept",
        ),
        ("empty content -> empty string", {"content": []}, ""),
        ("None msg -> empty string", None, ""),
    ]
    bad = []
    for label, inp, expected in cases:
        got = _extract_text(inp)
        if got != expected:
            bad.append(f"{label}: expected={expected!r} got={got!r}")
    report("T8 _extract_text covers shapes", not bad, "; ".join(bad))

    # T9 context defaults
    captured = _StubRunner([(_StubMsg("Friday", "assistant", "ok"), True)])
    await build_host_agent_runner(_make_provider(_StubWorkspace(captured)))(
        "gm",
        "p",
        {},
    )
    req = captured.captured_request
    defaults_ok = (
        req.user_id == DEFAULT_USER_ID
        and req.channel == DEFAULT_CHANNEL
        and req.session_id.startswith("hubos.core:gm:")
    )
    report("T9 context defaults applied", defaults_ok, repr(req.__dict__))

    # T10 naming hygiene on the source file
    src_text = _adapter_path.read_text(encoding="utf-8")
    # 'hubos' DOES appear in the file path docstring NOTE — but NOT in the file itself
    banned = []
    for term in ("openclaw", "hermes", "xclaw"):
        if re.search(rf"\b{term}\b", src_text, re.IGNORECASE):
            banned.append(term)
    # 'hubos' is allowed in this file (it's the host-app's own name);
    # we only forbid foreign project names here. hubos.core is fine.
    report(
        "T10 host adapter has no foreign project names",
        not banned,
        f"hits: {banned}",
    )

    print("")
    if failed:
        print(f"FAILED ({len(failed)}): {failed}")
        return 1
    print("ALL host_agent_runner adapter checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
