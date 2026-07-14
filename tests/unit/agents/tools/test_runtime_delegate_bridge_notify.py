import pytest
from agentscope_runtime.engine.schemas.agent_schemas import ContentType

from hubos.agents.tools import runtime_delegate
from hubos.app.channels.delivery_context import (
    DeliveryContext,
    reset_current_delivery_context,
    set_current_delivery_context,
)


def _text_from_response(response) -> str:
    first = response.content[0]
    if isinstance(first, dict):
        return first.get("text", "")
    return getattr(first, "text", "")


@pytest.mark.asyncio
async def test_bridge_completion_sends_delivery_notification():
    sent = []

    async def send_parts(to_handle, parts, meta):
        sent.append((to_handle, parts, meta))

    token = set_current_delivery_context(
        DeliveryContext(
            channel="feishu",
            to_handle="chat-1",
            meta={"receive_id": "chat-1"},
            send_parts=send_parts,
        ),
    )
    try:
        task_id = "bridge-test-notify"
        runtime_delegate._bridge_task_create(  # pylint: disable=protected-access
            task_id,
            exec_type="single",
            agent_id="research",
        )
        runtime_delegate._bridge_task_update(  # pylint: disable=protected-access
            task_id,
            status="done",
            result="final report",
        )

        await runtime_delegate._notify_bridge_task_completion(task_id)  # pylint: disable=protected-access
    finally:
        reset_current_delivery_context(token)
        with runtime_delegate._bridge_lock:  # pylint: disable=protected-access
            runtime_delegate._bridge_tasks.pop(  # pylint: disable=protected-access
                "bridge-test-notify",
                None,
            )

    assert sent
    assert sent[0][0] == "chat-1"
    assert "后台任务已完成" in sent[0][1][0].text
    assert "final report" in sent[0][1][0].text


@pytest.mark.asyncio
async def test_bridge_completion_sends_long_result_as_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HUBOS_BRIDGE_TASK_DIR", str(tmp_path / "bridge_tasks"))
    sent = []

    async def send_parts(to_handle, parts, meta):
        sent.append((to_handle, parts, meta))

    token = set_current_delivery_context(
        DeliveryContext(
            channel="feishu",
            to_handle="chat-1",
            meta={"receive_id": "chat-1"},
            send_parts=send_parts,
            workspace_id="feishu_user_1",
        ),
    )
    try:
        task_id = "bridge-test-long"
        long_result = "完整报告\n" + ("市场结论非常详细。\n" * 400)
        runtime_delegate._bridge_task_create(  # pylint: disable=protected-access
            task_id,
            exec_type="single",
            agent_id="research",
        )
        runtime_delegate._bridge_task_update(  # pylint: disable=protected-access
            task_id,
            status="done",
            result=long_result,
        )

        await runtime_delegate._notify_bridge_task_completion(task_id)  # pylint: disable=protected-access
    finally:
        reset_current_delivery_context(token)
        with runtime_delegate._bridge_lock:  # pylint: disable=protected-access
            runtime_delegate._bridge_tasks.pop(task_id, None)  # pylint: disable=protected-access

    assert sent
    parts = sent[0][1]
    assert "结果较长" in parts[0].text
    assert getattr(parts[1], "type", None) == ContentType.FILE
    file_path = parts[1].file_url.removeprefix("file://")
    assert "/workspaces/feishu_user_1/artifacts/bridge-test-long/" in file_path
    assert "完整报告" in open(file_path, encoding="utf-8").read()
    assert "已截断" not in open(file_path, encoding="utf-8").read()
    manifest_path = (
        tmp_path
        / ".hubos"
        / "workspaces"
        / "feishu_user_1"
        / "artifacts"
        / "bridge-test-long"
        / "artifact_manifest.json"
    )
    assert manifest_path.exists()
    assert (
        tmp_path
        / ".hubos"
        / "task_artifacts"
        / "bridge-test-long"
        / "artifact_manifest.json"
    ).exists()


def test_bridge_artifact_archive_rejects_owner_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HUBOS_TASK_ARTIFACT_DIR", str(tmp_path / "staged"))
    task_id = "bridge-owner-check"
    ctx_a = DeliveryContext(
        channel="feishu",
        to_handle="chat-a",
        meta={"receive_id": "chat-a"},
        send_parts=lambda *_args, **_kwargs: None,
        workspace_id="feishu_user_a",
    )
    ctx_b = DeliveryContext(
        channel="feishu",
        to_handle="chat-b",
        meta={"receive_id": "chat-b"},
        send_parts=lambda *_args, **_kwargs: None,
        workspace_id="feishu_user_b",
    )
    manifest = runtime_delegate._write_bridge_staged_artifact(  # pylint: disable=protected-access
        task_id=task_id,
        mode="single",
        result="secret report",
        delivery_ctx=ctx_a,
        record={"agent_id": "research"},
    )

    with pytest.raises(ValueError, match="Artifact owner mismatch"):
        runtime_delegate._archive_bridge_artifact_manifest(  # pylint: disable=protected-access
            manifest,
            ctx_b,
        )

    assert not (
        tmp_path
        / ".hubos"
        / "workspaces"
        / "feishu_user_b"
        / "artifacts"
        / task_id
    ).exists()


def test_bridge_artifact_archive_rejects_unsafe_workspace_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    staged_file = staged_dir / "result.md"
    staged_file.write_text("secret report", encoding="utf-8")
    manifest = {
        "task_id": "bridge-path-check",
        "owner_workspace_id": "../evil",
        "artifacts": [{"path": str(staged_file), "filename": staged_file.name}],
    }
    ctx = DeliveryContext(
        channel="feishu",
        to_handle="chat-a",
        meta={"receive_id": "chat-a"},
        send_parts=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ValueError, match="Unsafe workspace_id"):
        runtime_delegate._archive_bridge_artifact_manifest(  # pylint: disable=protected-access
            manifest,
            ctx,
        )

    assert not (tmp_path / ".hubos" / "evil").exists()


@pytest.mark.asyncio
async def test_bridge_completion_falls_back_when_artifact_archive_fails(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HUBOS_BRIDGE_TASK_DIR", str(tmp_path / "bridge_tasks"))
    sent = []

    async def send_parts(to_handle, parts, meta):
        sent.append((to_handle, parts, meta))

    token = set_current_delivery_context(
        DeliveryContext(
            channel="feishu",
            to_handle="chat-1",
            meta={"receive_id": "chat-1"},
            send_parts=send_parts,
            workspace_id="../evil",
        ),
    )
    task_id = "bridge-test-archive-fallback"
    try:
        runtime_delegate._bridge_task_create(  # pylint: disable=protected-access
            task_id,
            exec_type="single",
            agent_id="research",
        )
        runtime_delegate._bridge_task_update(  # pylint: disable=protected-access
            task_id,
            status="done",
            result="完整报告\n" + ("市场结论非常详细。\n" * 400),
        )

        await runtime_delegate._notify_bridge_task_completion(task_id)  # pylint: disable=protected-access
    finally:
        reset_current_delivery_context(token)
        with runtime_delegate._bridge_lock:  # pylint: disable=protected-access
            runtime_delegate._bridge_tasks.pop(task_id, None)  # pylint: disable=protected-access

    assert sent
    parts = sent[0][1]
    assert len(parts) == 1
    assert "归档失败" in parts[0].text
    assert "完整报告" in parts[0].text
    assert not (tmp_path / ".hubos" / "evil").exists()


@pytest.mark.asyncio
async def test_track_task_recovers_done_bridge_record_from_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBOS_BRIDGE_TASK_DIR", str(tmp_path / "bridge_tasks"))
    task_id = "bridge-test-persisted"
    runtime_delegate._bridge_task_create(  # pylint: disable=protected-access
        task_id,
        exec_type="single",
        agent_id="research",
    )
    runtime_delegate._bridge_task_update(  # pylint: disable=protected-access
        task_id,
        status="done",
        result="persisted final report",
    )
    with runtime_delegate._bridge_lock:  # pylint: disable=protected-access
        runtime_delegate._bridge_tasks.pop(task_id, None)  # pylint: disable=protected-access

    response = await runtime_delegate.track_task(task_id=task_id)
    text = response.content[0]["text"]

    assert "Status: done" in text
    assert "persisted final report" in text


def test_bridge_prefers_hr_for_document_translation_tasks():
    target = runtime_delegate._resolve_bridge_target(  # pylint: disable=protected-access
        "请把这个 xlsx 医疗采购表翻译成英文并保持原格式",
        "rd",
    )
    assert target == "hr"


@pytest.mark.asyncio
async def test_delegate_task_agent_bridge_blocks_nested_delegate():
    runtime_delegate.set_runtime_request_context(
        {
            "agent_id": "research",
            "channel": "feishu",
            "parent_session_id": "parent-1",
        },
    )
    try:
        response = await runtime_delegate._delegate_task_agent_bridge(  # pylint: disable=protected-access
            goal="继续转给 rd 处理",
            priority="normal",
            workflow="one_person_default",
            wait=True,
            timeout_seconds=30,
            extra_context={"agent_id": "rd"},
        )
    finally:
        runtime_delegate.set_runtime_request_context(None)

    assert "nested delegation is disabled" in _text_from_response(response)


def test_feishu_research_delegate_gets_fast_contract():
    token = runtime_delegate._runtime_request_ctx.set(  # pylint: disable=protected-access
        {
            "channel": "feishu",
            "session_id": "s1",
            "user_id": "u1",
        },
    )
    try:
        prompt = runtime_delegate._maybe_apply_feishu_research_fast_contract(  # pylint: disable=protected-access
            prompt="调研巴西医学教学模型市场",
            agent_id="research",
        )
        sales_prompt = runtime_delegate._maybe_apply_feishu_research_fast_contract(  # pylint: disable=protected-access
            prompt="开发客户",
            agent_id="sales",
        )
    finally:
        runtime_delegate._runtime_request_ctx.reset(token)  # pylint: disable=protected-access

    assert "飞书快速调研契约" in prompt
    assert "10 次 `web_search_prime`" in prompt
    assert "6 次 `webReader`" in prompt
    assert "原始任务" in prompt
    assert "调研巴西医学教学模型市场" in prompt
    assert sales_prompt == "开发客户"
