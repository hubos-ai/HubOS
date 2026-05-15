# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from .models import CronJobSpec

logger = logging.getLogger(__name__)


def _normalize_runner_input(value: Any) -> Any:
    """Normalize legacy cron prompt text to AgentRequest.input shape.

    Older cron jobs stored ``request.input`` as a plain string.  Runner
    ``stream_query`` validates dict requests as AgentRequest, whose ``input``
    must be a list of message objects.  Keep already-valid list inputs intact
    and wrap legacy strings just before execution so existing jobs continue to
    run without a data migration.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return [
            {
                "role": "user",
                "type": "message",
                "content": [{"type": "text", "text": text}],
            },
        ]
    return value


class CronExecutor:
    def __init__(self, *, runner: Any, channel_manager: Any):
        self._runner = runner
        self._channel_manager = channel_manager

    async def execute(self, job: CronJobSpec) -> None:
        """Execute one job once.

        - task_type text: send fixed text to channel
        - task_type agent: ask agent with prompt, send reply to channel (
            stream_query + send_event)
        """
        target_user_id = job.dispatch.target.user_id
        target_session_id = job.dispatch.target.session_id
        dispatch_meta: Dict[str, Any] = dict(job.dispatch.meta or {})
        logger.info(
            "cron execute: job_id=%s channel=%s task_type=%s "
            "target_user_id=%s target_session_id=%s",
            job.id,
            job.dispatch.channel,
            job.task_type,
            target_user_id[:40] if target_user_id else "",
            target_session_id[:40] if target_session_id else "",
        )

        if job.task_type == "text" and job.text:
            logger.info(
                "cron send_text: job_id=%s channel=%s len=%s",
                job.id,
                job.dispatch.channel,
                len(job.text or ""),
            )
            await self._channel_manager.send_text(
                channel=job.dispatch.channel,
                user_id=target_user_id,
                session_id=target_session_id,
                text=job.text.strip(),
                meta=dispatch_meta,
            )
            return

        # agent: run request as the dispatch target user so context matches
        logger.info(
            "cron agent: job_id=%s channel=%s stream_query then send_event",
            job.id,
            job.dispatch.channel,
        )
        assert job.request is not None
        req: Dict[str, Any] = job.request.model_dump(mode="json")
        req["input"] = _normalize_runner_input(req.get("input"))
        req["user_id"] = target_user_id or "cron"
        req["session_id"] = target_session_id or f"cron:{job.id}"

        # Set model override if configured (e.g. DeepSeek V4 Flash for cron)
        from ...config.context import set_current_model_override

        _model_token = None
        if job.request.model_override:
            _model_token = set_current_model_override(
                job.request.model_override
            )

        async def _run() -> None:
            async for event in self._runner.stream_query(req):
                await self._channel_manager.send_event(
                    channel=job.dispatch.channel,
                    user_id=target_user_id,
                    session_id=target_session_id,
                    event=event,
                    meta=dispatch_meta,
                )

        try:
            await asyncio.wait_for(
                _run(),
                timeout=job.runtime.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "cron execute: job_id=%s timed out after %ss",
                job.id,
                job.runtime.timeout_seconds,
            )
            raise
        except asyncio.CancelledError:
            logger.info("cron execute: job_id=%s cancelled", job.id)
            raise
        finally:
            # Clear model override ContextVar
            if _model_token is not None:
                from ...config.context import current_model_override

                current_model_override.reset(_model_token)
