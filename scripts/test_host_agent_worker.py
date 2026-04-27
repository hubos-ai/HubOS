# -*- coding: utf-8 -*-
"""Stage B step 1: end-to-end test for HostAgentWorker.

Verifies the new generic worker:
  T1  successful path  -> WorkerResult populated
  T2  prompt aliases   -> all of {prompt, input_text, goal, query, content} work
  T3  missing prompt   -> WorkerExecutionError
  T4  None response    -> WorkerExecutionError
  T5  runner raises    -> WorkerExecutionError (wrapped)
  T6  runner times out -> WorkerTimeoutError
  T7  cancellation     -> propagated as CancelledError, not swallowed
  T8  supports()       -> respects defaults + custom set
  T9  custom name+conf -> name_override + default_confidence flow through
  T10 naming hygiene   -> source file mentions no banned project names

Run: python3 scripts/test_host_agent_worker.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import traceback
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from hubos.core.workers.providers.base import (  # noqa: E402
    WorkerExecutionError,
    WorkerResult,
    WorkerTimeoutError,
)
from hubos.core.workers.providers.host_agent import (  # noqa: E402
    HostAgentWorker,
)


PASS = "[PASS]"
FAIL = "[FAIL]"


def _ok(name: str) -> None:
    print(f"{PASS} {name}")


def _bad(name: str, detail: str) -> None:
    print(f"{FAIL} {name}: {detail}")


async def _run() -> int:
    failed: list[str] = []

    def report(name: str, ok: bool, detail: str = "") -> None:
        if ok:
            _ok(name)
        else:
            _bad(name, detail)
            failed.append(name)

    # ---------- T1: success path ----------
    async def runner_ok(agent_id: str, prompt: str, context: dict) -> str:
        return f"echo[{agent_id}]: {prompt}"

    w = HostAgentWorker(agent_id="gm", runner=runner_ok)
    unit = uuid4()
    res = await w.execute(unit, {"prompt": "hello"}, timeout_seconds=5)
    ok = (
        isinstance(res, WorkerResult)
        and res.success
        and res.unit_id == unit
        and res.provider == "host_agent:gm"
        and res.data["content"] == "echo[gm]: hello"
        and res.data["agent_id"] == "gm"
        and res.error is None
        and 0 <= res.confidence <= 1
        and res.execution_time_ms >= 0
    )
    report("T1 success path produces well-formed WorkerResult", ok, repr(res))

    # ---------- T2: prompt key aliases ----------
    aliases = ["prompt", "input_text", "goal", "query", "content"]
    bad_aliases: list[str] = []
    for key in aliases:
        r = await w.execute(uuid4(), {key: f"via-{key}"}, timeout_seconds=5)
        if not (r.success and r.data["content"].endswith(f"via-{key}")):
            bad_aliases.append(key)
    report(
        "T2 all prompt key aliases accepted",
        not bad_aliases,
        f"failed: {bad_aliases}",
    )

    # ---------- T3: missing prompt ----------
    try:
        await w.execute(uuid4(), {"unrelated": "x"}, timeout_seconds=5)
        report(
            "T3 missing prompt raises WorkerExecutionError",
            False,
            "no exception raised",
        )
    except WorkerExecutionError as e:
        report("T3 missing prompt raises WorkerExecutionError", True, "")
        if "prompt" not in str(e).lower():
            report("T3a error message hints at prompt keys", False, str(e))
        else:
            report("T3a error message hints at prompt keys", True, "")
    except Exception as e:  # noqa: BLE001
        report(
            "T3 missing prompt raises WorkerExecutionError",
            False,
            f"got {type(e).__name__}: {e}",
        )

    # ---------- T4: None response ----------
    async def runner_none(agent_id, prompt, context):
        return None  # pyright: ignore

    w_none = HostAgentWorker(agent_id="gm", runner=runner_none)  # type: ignore[arg-type]
    try:
        await w_none.execute(uuid4(), {"prompt": "x"}, timeout_seconds=5)
        report(
            "T4 None response wrapped as WorkerExecutionError",
            False,
            "no exception",
        )
    except WorkerExecutionError:
        report("T4 None response wrapped as WorkerExecutionError", True, "")
    except Exception as e:  # noqa: BLE001
        report(
            "T4 None response wrapped as WorkerExecutionError",
            False,
            f"got {type(e).__name__}: {e}",
        )

    # ---------- T5: runner raises ----------
    async def runner_raises(agent_id, prompt, context):
        raise RuntimeError("kaboom")

    w_raise = HostAgentWorker(agent_id="gm", runner=runner_raises)
    try:
        await w_raise.execute(uuid4(), {"prompt": "x"}, timeout_seconds=5)
        report(
            "T5 runner exception wrapped as WorkerExecutionError",
            False,
            "no exception",
        )
    except WorkerExecutionError as e:
        ok = "kaboom" in str(e) or "RuntimeError" in str(e)
        report(
            "T5 runner exception wrapped as WorkerExecutionError",
            ok,
            str(e),
        )
    except Exception as e:  # noqa: BLE001
        report(
            "T5 runner exception wrapped as WorkerExecutionError",
            False,
            f"got {type(e).__name__}: {e}",
        )

    # ---------- T6: timeout ----------
    async def runner_slow(agent_id, prompt, context):
        await asyncio.sleep(2)
        return "too late"

    w_slow = HostAgentWorker(agent_id="gm", runner=runner_slow)
    try:
        await w_slow.execute(uuid4(), {"prompt": "x"}, timeout_seconds=1)
        report(
            "T6 slow runner triggers WorkerTimeoutError",
            False,
            "no exception",
        )
    except WorkerTimeoutError as e:
        report(
            "T6 slow runner triggers WorkerTimeoutError",
            "timed out" in str(e).lower(),
            str(e),
        )
    except Exception as e:  # noqa: BLE001
        report(
            "T6 slow runner triggers WorkerTimeoutError",
            False,
            f"got {type(e).__name__}: {e}",
        )

    # ---------- T7: cancellation propagates ----------
    started = asyncio.Event()
    cancelled_inside = asyncio.Event()

    async def runner_cancellable(agent_id, prompt, context):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled_inside.set()
            raise
        return "never"

    w_cancel = HostAgentWorker(agent_id="gm", runner=runner_cancellable)

    async def _run_cancel():
        return await w_cancel.execute(
            uuid4(),
            {"prompt": "x"},
            timeout_seconds=30,
        )

    task = asyncio.create_task(_run_cancel())
    await started.wait()
    task.cancel()
    cancelled_propagated = False
    try:
        await task
    except asyncio.CancelledError:
        cancelled_propagated = True
    except Exception as e:  # noqa: BLE001
        report(
            "T7 cancellation propagates as CancelledError",
            False,
            f"got {type(e).__name__}: {e}",
        )
    report(
        "T7 cancellation propagates as CancelledError",
        cancelled_propagated and cancelled_inside.is_set(),
        f"propagated={cancelled_propagated} inside={cancelled_inside.is_set()}",
    )

    # ---------- T8: supports() ----------
    w_default = HostAgentWorker(agent_id="gm", runner=runner_ok)
    s_default_ok = (
        w_default.supports("research")
        and w_default.supports("ANALYSIS")  # case-insensitive
        and not w_default.supports("nonsense")
        and not w_default.supports("")
    )
    report(
        "T8 default supports() covers known + rejects unknown/empty",
        s_default_ok,
        "",
    )

    w_custom = HostAgentWorker(
        agent_id="gm",
        runner=runner_ok,
        supported_tasks={"only_this"},
    )
    s_custom_ok = w_custom.supports("only_this") and not w_custom.supports(
        "research",
    )
    report("T8b custom supported_tasks restricts the set", s_custom_ok, "")

    # ---------- T9: name_override + default_confidence ----------
    w_named = HostAgentWorker(
        agent_id="gm",
        runner=runner_ok,
        name_override="my_team_lead",
        default_confidence=0.42,
    )
    r = await w_named.execute(uuid4(), {"prompt": "hi"}, timeout_seconds=5)
    ok = (
        w_named.name == "my_team_lead"
        and r.provider == "my_team_lead"
        and abs(r.confidence - 0.42) < 1e-9
        and w_named.agent_id == "gm"
    )
    report("T9 name_override + default_confidence flow through", ok, repr(r))

    # ---------- T10: naming hygiene on the source file ----------
    # HubOS is our own project name, so it's allowed.  The list below names
    # legacy or sibling projects that must NOT leak into the in-process
    # worker adapter — it stays neutral so the subpackage can be lifted
    # out and reused independently.
    src = (
        SRC / "hubos" / "core" / "workers" / "providers" / "host_agent.py"
    ).read_text(encoding="utf-8")
    banned = []
    for term in ("openclaw", "copaw", "solo_hub", "hermes", "xclaw"):
        if re.search(rf"\b{term}\b", src, re.IGNORECASE):
            banned.append(term)
    report(
        "T10 host_agent.py contains no banned project names",
        not banned,
        f"hits: {banned}",
    )

    print("")
    if failed:
        print(f"FAILED ({len(failed)}): {failed}")
        return 1
    print("ALL HostAgentWorker checks passed.")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
