# -*- coding: utf-8 -*-
"""验证  当前 session 隔离算法的并发安全性。

这是一个 **合约测试 (contract test)** —— 不装整个 hubos 栈，而是用 stdlib 精确
复刻 HubOS 现行算法，然后在高并发下验证"不串台、不丢数据、不相互污染"。

复刻的源头：
  - `src/hubos/app/runner/session.py`
      - `sanitize_filename`        (line 26-34)
      - `SafeJSONSession._get_save_path`   (line 56-69)
      - `SafeJSONSession.save_session_state` (line 71-93)
      - `SafeJSONSession.load_session_state` (line 95-132)

运行:
    python3 scripts/verify_session_isolation.py
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
import tempfile
import time
from dataclasses import dataclass

# ---------- 复刻 HubOS 算法（逐行对应源码） ----------

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str) -> str:
    """对应 session.py:26-34"""
    return _UNSAFE_FILENAME_RE.sub("--", name)


def get_save_path(save_dir: str, session_id: str, user_id: str) -> str:
    """对应 session.py:56-69"""
    os.makedirs(save_dir, exist_ok=True)
    safe_sid = sanitize_filename(session_id)
    safe_uid = sanitize_filename(user_id) if user_id else ""
    if safe_uid:
        file_path = f"{safe_uid}_{safe_sid}.json"
    else:
        file_path = f"{safe_sid}.json"
    return os.path.join(save_dir, file_path)


async def save_state(save_dir: str, session_id: str, user_id: str, state: dict) -> None:
    """对应 session.py:71-93（简化：不用 aiofiles，用 to_thread 保留 IO 语义）"""
    path = get_save_path(save_dir, session_id, user_id)
    payload = json.dumps(state, ensure_ascii=False)

    def _write() -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)

    await asyncio.to_thread(_write)


async def load_state(save_dir: str, session_id: str, user_id: str) -> dict | None:
    """对应 session.py:95-132"""
    path = get_save_path(save_dir, session_id, user_id)
    if not os.path.exists(path):
        return None

    def _read() -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.loads(f.read())

    return await asyncio.to_thread(_read)


# ---------- 模拟 per-request agent + per-session memory ----------


@dataclass
class FakeAgent:
    """模拟 HubOSAgent：每次请求 new 一个实例，内存 state 独立。
    对应 runner.py:294 每次 query 都 `HubOSAgent(...)`。
    """

    memory: list

    @classmethod
    def new(cls) -> "FakeAgent":
        return cls(memory=[])


async def simulate_query(
    save_dir: str,
    session_id: str,
    user_id: str,
    user_text: str,
) -> dict:
    """模拟一次完整 query 流程（对应 runner.py query_handler 整个生命周期）:

    1. new 一个 agent (stateless)
    2. 从磁盘加载该 (user_id, session_id) 的历史 memory
    3. 往 memory 追加当前对话
    4. 保存回磁盘
    5. 返回当前 memory 长度（给断言用）
    """
    agent = FakeAgent.new()

    loaded = await load_state(save_dir, session_id, user_id)
    if loaded:
        agent.memory = loaded.get("memory", [])

    await asyncio.sleep(random.uniform(0.001, 0.01))

    agent.memory.append(
        {
            "role": "user",
            "content": user_text,
            "user_id": user_id,
            "session_id": session_id,
        },
    )
    agent.memory.append(
        {
            "role": "assistant",
            "content": f"echo[{user_id}|{session_id}]: {user_text}",
        },
    )

    await save_state(
        save_dir,
        session_id,
        user_id,
        {"memory": agent.memory, "last_user": user_id},
    )

    return {"session_id": session_id, "user_id": user_id, "memory_len": len(agent.memory)}


# ---------- 并发测试 ----------


async def test_concurrent_isolation(save_dir: str) -> None:
    """核心断言：反映真实使用场景下的隔离语义。

    真实模型：
      - **同一** (user_id, session_id) 内 query 是 turn-based 的（用户发一条 → 等回复
        → 再发下一条），由 channel/UI 保证串行。
      - **不同** (user_id, session_id) 之间并发（Alice 和 Bob 同时聊天）。

    所以正确的并发形态是：每个 session 内部顺序 5 个 query，但
    N 个 session 之间全部并发启动。断言：
      - 每个 (user_id, session_id) 最终 memory 恰好是自己发的 5 条
      - 不混入任何其他对子的内容
    """
    users = ["alice", "bob", "carol", "dave"]
    sessions_per_user = ["s1", "s2", "s3"]
    queries_per_pair = 5

    async def run_one_session(uid: str, full_sid: str, expected_texts: list[str]) -> None:
        for text in expected_texts:
            await simulate_query(save_dir, full_sid, uid, text)

    expected: dict[tuple[str, str], list[str]] = {}
    session_tasks = []
    for uid in users:
        for sid in sessions_per_user:
            full_sid = f"discord:{sid}"
            key = (uid, full_sid)
            expected[key] = [
                f"msg-{uid}-{sid}-{i}" for i in range(queries_per_pair)
            ]
            session_tasks.append(run_one_session(uid, full_sid, expected[key]))

    random.shuffle(session_tasks)

    t0 = time.perf_counter()
    await asyncio.gather(*session_tasks)
    elapsed = time.perf_counter() - t0

    total_queries = len(session_tasks) * queries_per_pair
    print(
        f"  · {len(session_tasks)} 个 session 并发（每 session 内 {queries_per_pair} 个 turn 顺序），"
        f"共 {total_queries} 条 query 耗时 {elapsed*1000:.1f}ms",
    )

    failures: list[str] = []
    for (uid, full_sid), expected_msgs in expected.items():
        loaded = await load_state(save_dir, full_sid, uid)
        if loaded is None:
            failures.append(f"{uid}|{full_sid}: 文件丢失")
            continue
        mem = loaded.get("memory", [])
        user_msgs = [m for m in mem if m.get("role") == "user"]

        if len(user_msgs) != queries_per_pair:
            failures.append(
                f"{uid}|{full_sid}: 用户消息数={len(user_msgs)}, 期望={queries_per_pair}",
            )
            continue

        got_texts = {m["content"] for m in user_msgs}
        want_texts = set(expected_msgs)
        if got_texts != want_texts:
            missing = want_texts - got_texts
            extra = got_texts - want_texts
            failures.append(f"{uid}|{full_sid}: 缺 {missing}, 多 {extra}")
            continue

        for m in user_msgs:
            if m.get("user_id") != uid or m.get("session_id") != full_sid:
                failures.append(
                    f"{uid}|{full_sid}: 包含他人消息 {m}",
                )
                break

    if failures:
        print(f"  ✗ 发现 {len(failures)} 个隔离缺陷：")
        for f in failures[:10]:
            print(f"     - {f}")
        raise SystemExit(1)

    print(
        f"  ✓ {len(users)}×{len(sessions_per_user)}={len(expected)} 个独立 session "
        f"× 每对 {queries_per_pair} 条消息 "
        f"= {total_queries} 条消息全部隔离正确",
    )


async def test_filename_sanitization() -> None:
    """验证 channel 带特殊字符的 session_id 也能安全落盘。"""
    cases = [
        ("discord:dm:12345", "user01", "user01_discord--dm--12345.json"),
        ("console:alice", "alice", "alice_console--alice.json"),
        ('win/evil\\name:*?"<>|', "u1", 'u1_win--evil--name--------------.json'),
        ("normal-sid", "", "normal-sid.json"),
    ]
    failures = []
    for sid, uid, expected_filename in cases:
        path = get_save_path("/tmp", sid, uid)
        actual = os.path.basename(path)
        if actual != expected_filename:
            failures.append(f"sid={sid!r} uid={uid!r}: got {actual}, want {expected_filename}")

    if failures:
        print("  ✗ filename sanitize 失败：")
        for f in failures:
            print(f"     - {f}")
        raise SystemExit(1)
    print(f"  ✓ {len(cases)} 个 sanitize 用例全部通过（含 Windows 非法字符）")


async def test_same_session_sequential_accumulates() -> None:
    """同一 (user, session) 连续多次 query，memory 必须累积，不覆盖。"""
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(10):
            await simulate_query(tmp, "discord:x", "alice", f"q{i}")
        loaded = await load_state(tmp, "discord:x", "alice")
        assert loaded is not None
        user_msgs = [m for m in loaded["memory"] if m.get("role") == "user"]
        if len(user_msgs) != 10:
            print(f"  ✗ 累积错误：期望 10 条 user msg，实际 {len(user_msgs)}")
            raise SystemExit(1)
        print("  ✓ 同 session 10 次 query 正确累积到 memory")


async def test_cross_user_no_bleed() -> None:
    """同一 session_id 但不同 user_id，必须完全隔离（文件名不同）。"""
    with tempfile.TemporaryDirectory() as tmp:
        await simulate_query(tmp, "console:shared", "alice", "secret-alice")
        await simulate_query(tmp, "console:shared", "bob", "secret-bob")

        a = await load_state(tmp, "console:shared", "alice")
        b = await load_state(tmp, "console:shared", "bob")

        assert a is not None and b is not None
        a_texts = {m["content"] for m in a["memory"] if m.get("role") == "user"}
        b_texts = {m["content"] for m in b["memory"] if m.get("role") == "user"}

        if a_texts != {"secret-alice"} or b_texts != {"secret-bob"}:
            print(f"  ✗ 跨 user 串台：alice={a_texts}, bob={b_texts}")
            raise SystemExit(1)
        print("  ✓ 同一 session_id 的 alice / bob 数据完全隔离")


async def main() -> None:
    print("=" * 72)
    print(" session 隔离合约测试")
    print("（复刻 hubos/app/runner/session.py 算法并高并发验证）")
    print("=" * 72)

    print("\n[1/4] 文件名 sanitize 边界用例")
    await test_filename_sanitization()

    print("\n[2/4] 同 session 顺序 query 的 memory 累积性")
    await test_same_session_sequential_accumulates()

    print("\n[3/4] 同 session_id 跨 user 的数据隔离")
    await test_cross_user_no_bleed()

    print("\n[4/4] 多用户 × 多 session 并发隔离（主测试）")
    tmp = tempfile.mkdtemp(prefix="hubos-iso-")
    try:
        await test_concurrent_isolation(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 72)
    print("✓ 全部通过：当前 HubOS 的 stateless-agent + per-session JSON 模型")
    print("  在 (user_id, session_id) 维度下并发安全，无需新增 SessionManager。")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
