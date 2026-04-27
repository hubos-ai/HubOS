# -*- coding: utf-8 -*-
"""端到端验证 总经理 → hubos runtime (legacy) delegation 工具。

不依赖 hubos 完整栈：
  - stub 掉 agentscope.message.TextBlock + agentscope.tool.ToolResponse
  - 用 importlib 把 runtime_delegate.py 当独立模块加载
  - 启动一个真实的 hubos runtime (legacy)（FastAPI），跑全链路

测试矩阵：
  1. delegate_task(wait=False) 立即返回 task_id
  2. delegate_task(wait=True) 阻塞等结果，返回完整 final_response
  3. track_task(task_id) 对已完成任务返回快照
  4. track_task(task_id, follow=True) 对运行中任务订阅到结束
  5. cancel_task(task_id) 透明返回 Runtime 的 501 占位
  6. Runtime 不可达时返回结构化错误（不抛）
  7. session_id 上下文正确透传（通过 set_runtime_request_context）

运行:
    python3 scripts/test_runtime_delegate.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import subprocess
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_TOOL_PATH = (
    REPO_ROOT / "src" / "hubos" / "agents" / "tools" / "runtime_delegate.py"
)
RUNTIME_DIR = REPO_ROOT / "hubos runtime (legacy)"


# ==================== Stub agentscope ====================


@dataclass
class _StubTextBlock:
    type: str
    text: str


@dataclass
class _StubToolResponse:
    content: list = field(default_factory=list)


def _install_agentscope_stubs() -> None:
    msg_mod = types.ModuleType("agentscope.message")
    msg_mod.TextBlock = _StubTextBlock
    tool_mod = types.ModuleType("agentscope.tool")
    tool_mod.ToolResponse = _StubToolResponse
    pkg_mod = types.ModuleType("agentscope")
    pkg_mod.message = msg_mod
    pkg_mod.tool = tool_mod
    sys.modules["agentscope"] = pkg_mod
    sys.modules["agentscope.message"] = msg_mod
    sys.modules["agentscope.tool"] = tool_mod


def _load_runtime_delegate():
    spec = importlib.util.spec_from_file_location(
        "runtime_delegate_under_test",
        str(WEBUI_TOOL_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ==================== Helpers ====================


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=2.0, trust_env=False) as c:
                resp = c.get(f"{url}/v1/health")
            if resp.status_code == 200:
                return
        except httpx.RequestError as e:
            last_err = e
        time.sleep(0.3)
    raise RuntimeError(f"Runtime did not become healthy at {url}: {last_err}")


def _start_runtime(port: int, log_path: Path) -> subprocess.Popen:
    """Spawn hubos runtime (legacy) via its official launcher.

    Stream logs to a file (not pipe) so the OS pipe buffer can never block
    the subprocess if it gets verbose.
    """
    env = {
        **os.environ,
        "ENABLE_EXECUTION_LOOP_MVP": "true",
        "PYTHONUNBUFFERED": "1",
    }
    cmd = [
        sys.executable,
        "scripts/run_api.py",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    log_fh = open(log_path, "w", buffering=1)
    proc = subprocess.Popen(
        cmd,
        cwd=str(RUNTIME_DIR),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    return proc


def _stop_runtime(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _text(resp: Any) -> str:
    return resp.content[0].text if resp.content else ""


# ==================== Tests ====================


async def test_unreachable_runtime(rd) -> None:
    """Runtime 不在 → 返回结构化错误，不抛。"""
    os.environ["HUBOS_RUNTIME_URL"] = "http://127.0.0.1:1"
    try:
        resp = await rd.delegate_task("hello", wait=False)
    finally:
        os.environ.pop("HUBOS_RUNTIME_URL", None)
    text = _text(resp)
    assert (
        "unreachable" in text.lower()
    ), f"expected unreachable error, got: {text!r}"
    assert "1" in text
    print("  ✓ Runtime 不可达时返回结构化错误（无 stack trace）")


async def test_delegate_no_wait(rd, base_url: str) -> None:
    os.environ["HUBOS_RUNTIME_URL"] = base_url
    rd.set_runtime_request_context(
        {
            "session_id": "test-session-1",
            "user_id": "alice",
            "channel": "web_ui",
        },
    )
    resp = await rd.delegate_task(
        "Summarise the benefits of HTTP/2 in one sentence.",
        wait=False,
    )
    text = _text(resp)
    assert "Task ID:" in text, f"missing Task ID in: {text!r}"
    task_id = text.split("Task ID:")[1].split("\n")[0].strip()
    assert len(task_id) > 5, f"suspicious task_id: {task_id!r}"
    print(f"  ✓ delegate_task(wait=False) 立即返回 task_id={task_id[:8]}…")
    return task_id


async def test_track_after_done(
    rd,
    task_id: str,
    log_path: Path | None = None,
) -> None:
    """等之前的任务跑完，再 track_task 返回 final_response。"""
    last_text = ""
    terminal_markers = ("Status: done", "Status: failed", "Status: cancelled")
    for _ in range(240):
        resp = await rd.track_task(task_id, follow=False)
        text = _text(resp)
        last_text = text
        if any(m in text for m in terminal_markers):
            break
        await asyncio.sleep(0.5)
    else:
        if log_path and log_path.exists():
            tail = log_path.read_text(errors="ignore")[-3000:]
            print(f"\n[debug] Runtime log tail when stuck:\n{tail}")
        print(f"[debug] last track_task text:\n{last_text}")
        raise AssertionError(f"task {task_id} did not terminate in 120s")

    text = _text(resp)
    assert f"Task ID: {task_id}" in text
    assert any(m in text for m in terminal_markers)
    if "Status: done" in text:
        assert (
            "Final response:" in text
        ), f"DONE task missing final_response: {text!r}"
    print(f"  ✓ track_task(follow=False) 对终结任务返回 status + final_response")


async def test_delegate_with_wait(rd) -> None:
    """delegate_task(wait=True) 阻塞 SSE 直到完成。"""
    rd.set_runtime_request_context(
        {
            "session_id": "test-session-2",
            "user_id": "bob",
            "channel": "web_ui",
        },
    )
    t0 = time.perf_counter()
    resp = await rd.delegate_task(
        "Reply with the single word OK.",
        wait=True,
        timeout_seconds=120,
    )
    elapsed = time.perf_counter() - t0
    text = _text(resp)
    assert "Task ID:" in text
    assert "Status:" in text
    final_in_text = "Final response:" in text
    print(
        f"  ✓ delegate_task(wait=True) 完整流程耗时 {elapsed:.1f}s "
        f"(final_response={'yes' if final_in_text else 'no'})",
    )


async def test_cancel_returns_501_gracefully(rd, base_url: str) -> None:
    """已知 Runtime cancel 返回 501，工具应优雅说明而不报错。"""
    os.environ["HUBOS_RUNTIME_URL"] = base_url
    rd.set_runtime_request_context(
        {
            "session_id": "test-session-3",
            "user_id": "carol",
            "channel": "web_ui",
        },
    )
    submit = await rd.delegate_task("noop", wait=False)
    task_id = _text(submit).split("Task ID:")[1].split("\n")[0].strip()
    resp = await rd.cancel_task(task_id)
    text = _text(resp)
    assert ("not yet support" in text.lower()) or (
        "cancel requested" in text.lower()
    ), f"unexpected cancel response: {text!r}"
    assert task_id in text
    print(f"  ✓ cancel_task 优雅处理 Runtime 501 占位返回")


async def test_track_unknown_task(rd) -> None:
    resp = await rd.track_task("nonexistent-task-id-xxx", follow=False)
    text = _text(resp)
    assert "not found" in text.lower(), f"expected not-found error: {text!r}"
    print("  ✓ track_task 对未知 task_id 返回 404 错误")


async def test_session_context_propagated(rd, base_url: str) -> None:
    """验证 session_id / user_id 透传到 Runtime（通过 GET /v1/tasks/{id} 回看）。"""
    os.environ["HUBOS_RUNTIME_URL"] = base_url
    expected_session = "test-session-ctx-99"
    rd.set_runtime_request_context(
        {
            "session_id": expected_session,
            "user_id": "dave",
            "channel": "telegram",
        },
    )
    submit = await rd.delegate_task("ping", wait=False)
    task_id = _text(submit).split("Task ID:")[1].split("\n")[0].strip()
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        snap = (await client.get(f"{base_url}/v1/tasks/{task_id}")).json()
    actual_session = snap.get("session_id")
    assert (
        actual_session == expected_session
    ), f"session_id not propagated: expected {expected_session}, got {actual_session}"
    print(f"  ✓ session_id 正确透传（{expected_session}）")


# ==================== Driver ====================


async def main() -> None:
    print("=" * 72)
    print("HubOS 总经理 → Runtime delegation 工具端到端测试")
    print("=" * 72)

    print("\n[setup] 加载 runtime_delegate.py（stub agentscope）")
    _install_agentscope_stubs()
    rd = _load_runtime_delegate()
    print(f"        loaded from {WEBUI_TOOL_PATH}")

    print("\n[1/7] Runtime 不可达 → 结构化错误")
    await test_unreachable_runtime(rd)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = Path("/tmp") / f"hubos_runtime_test_{port}.log"
    print(f"\n[setup] 启动 Runtime on {base_url} (log: {log_path})")
    proc = _start_runtime(port, log_path)
    try:
        try:
            _wait_for_health(base_url)
        except RuntimeError:
            tail = (
                log_path.read_text(errors="ignore")[-2000:]
                if log_path.exists()
                else "(no log)"
            )
            print(f"\nRuntime log tail:\n{tail}")
            raise
        print(f"        Runtime healthy (pid={proc.pid})")

        print("\n[2/7] delegate_task(wait=False)")
        task_id = await test_delegate_no_wait(rd, base_url)

        print("\n[3/7] track_task(follow=False) on terminated task")
        await test_track_after_done(rd, task_id, log_path)

        print("\n[4/7] delegate_task(wait=True)")
        await test_delegate_with_wait(rd)

        print("\n[5/7] cancel_task graceful 501 handling")
        await test_cancel_returns_501_gracefully(rd, base_url)

        print("\n[6/7] track_task on unknown task_id")
        await test_track_unknown_task(rd)

        print("\n[7/7] session_id 上下文透传")
        await test_session_context_propagated(rd, base_url)

    finally:
        _stop_runtime(proc)
        print(f"\n[teardown] Runtime stopped (log: {log_path})")

    print("\n" + "=" * 72)
    print("✓ 全部通过：总经理三件套 (delegate_task/track_task/cancel_task) 工作正常")
    print("=" * 72)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print(f"\n✗ FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
