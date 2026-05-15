# -*- coding: utf-8 -*-
import pytest

from hubos.app.crons.executor import CronExecutor, _normalize_runner_input
from hubos.app.crons.models import CronJobSpec


class FakeRunner:
    def __init__(self):
        self.requests = []

    async def stream_query(self, req):
        self.requests.append(req)
        if False:
            yield None


class FakeChannelManager:
    async def send_event(self, **_kwargs):
        return None

    async def send_text(self, **_kwargs):
        return None


def _job(input_value):
    return CronJobSpec(
        id="job-1",
        name="legacy input job",
        schedule={
            "type": "cron",
            "cron": "0 23 * * *",
            "timezone": "Asia/Shanghai",
        },
        task_type="agent",
        request={"input": input_value, "session_id": "old", "user_id": "old"},
        dispatch={
            "type": "channel",
            "channel": "console",
            "target": {"user_id": "default", "session_id": "cron_test"},
            "mode": "final",
            "meta": {},
        },
        runtime={"timeout_seconds": 5},
    )


def test_normalize_runner_input_wraps_legacy_string():
    normalized = _normalize_runner_input("  run maintenance  ")
    assert normalized == [
        {
            "role": "user",
            "type": "message",
            "content": [{"type": "text", "text": "run maintenance"}],
        },
    ]


def test_normalize_runner_input_keeps_message_list():
    value = [
        {
            "role": "user",
            "type": "message",
            "content": [{"type": "text", "text": "already valid"}],
        },
    ]
    assert _normalize_runner_input(value) is value


@pytest.mark.asyncio
async def test_cron_executor_wraps_legacy_string_before_stream_query():
    runner = FakeRunner()
    executor = CronExecutor(
        runner=runner, channel_manager=FakeChannelManager()
    )

    await executor.execute(_job("legacy prompt"))

    assert len(runner.requests) == 1
    req = runner.requests[0]
    assert req["user_id"] == "default"
    assert req["session_id"] == "cron_test"
    assert req["input"] == [
        {
            "role": "user",
            "type": "message",
            "content": [{"type": "text", "text": "legacy prompt"}],
        },
    ]
