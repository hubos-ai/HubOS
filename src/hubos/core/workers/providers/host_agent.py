"""Host-application agent adapter as a hubos.core WorkerProvider.

This module deliberately avoids importing anything from the surrounding host
application (e.g. its agent classes, manager, framework). The host injects a
small async callable, which keeps :mod:`hubos.core` independent and reusable
under any host runtime.

Typical wiring (done in host startup code, NOT here)::

    from hubos.core.workers.providers.host_agent import HostAgentWorker

    async def my_host_runner(agent_id: str, prompt: str, context: dict) -> str:
        # Whatever the host knows how to do: spin up an agent, run it,
        # collect the final assistant text. Return as plain string.
        ...

    worker = HostAgentWorker(agent_id="gm", runner=my_host_runner)
    coordinator = Coordinator(worker_registry={worker.name: worker})

The worker handles cancellation, timeout, error normalization and packaging
into :class:`WorkerResult`; the host runner only needs to do "given prompt,
return assistant text".
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from hubos.core.workers.providers.base import (
    WorkerExecutionError,
    WorkerProvider,
    WorkerProviderError,
    WorkerResult,
    WorkerTimeoutError,
)

logger = logging.getLogger(__name__)


HostAgentRunner = Callable[[str, str, dict[str, Any]], Awaitable[str]]
"""(agent_id, prompt, context) -> response_text.

Implementations should:
- treat ``prompt`` as a single self-contained natural-language request
- treat ``context`` as best-effort hints (session_id, user_id, channel, …);
  ignoring it is acceptable
- return the final assistant text as a plain string
- raise on unrecoverable errors (timeout/cancellation will be wrapped by the
  worker into ``WorkerTimeoutError`` / ``WorkerExecutionError``)
"""


class HostAgentWorker(WorkerProvider):
    """Wrap any async ``HostAgentRunner`` as a hubos.core :class:`WorkerProvider`.

    Knows nothing about the host application's internals — only that the host
    can produce text given a prompt. This keeps the boundary tight and lets
    multiple host implementations (CLI, FastAPI, embedded) share the same
    Coordinator without forking ``hubos.core``.
    """

    DEFAULT_SUPPORTED_TASKS = frozenset({
        "general",
        "research",
        "analysis",
        "summary",
        "execution",
        "planning",
        "review",
    })

    DEFAULT_PROMPT_KEYS = ("prompt", "input_text", "goal", "query", "content")

    def __init__(
        self,
        agent_id: str,
        runner: HostAgentRunner,
        name_override: Optional[str] = None,
        supported_tasks: Optional[set[str]] = None,
        default_confidence: float = 0.9,
    ) -> None:
        if not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if runner is None or not callable(runner):
            raise ValueError("runner must be an async callable")
        self._agent_id = agent_id
        self._runner = runner
        self._name = name_override or f"host_agent:{agent_id}"
        self._supported = (
            frozenset(t.lower() for t in supported_tasks)
            if supported_tasks is not None
            else self.DEFAULT_SUPPORTED_TASKS
        )
        self._default_confidence = max(0.0, min(1.0, float(default_confidence)))

    @property
    def name(self) -> str:
        return self._name

    @property
    def agent_id(self) -> str:
        """The host-side agent identifier this worker dispatches to."""
        return self._agent_id

    async def execute(
        self,
        unit_id: UUID,
        input_data: dict[str, Any],
        timeout_seconds: int,
    ) -> WorkerResult:
        prompt = self._extract_prompt(input_data)
        if not prompt:
            raise WorkerExecutionError(
                f"input_data must contain one of {self.DEFAULT_PROMPT_KEYS!r}"
            )
        context = dict(input_data.get("context") or {})

        start = time.time()
        logger.info(
            "HostAgentWorker dispatching",
            extra={
                "unit_id": str(unit_id),
                "agent_id": self._agent_id,
                "timeout_seconds": timeout_seconds,
                "prompt_chars": len(prompt),
            },
        )

        try:
            response_text = await asyncio.wait_for(
                self._runner(self._agent_id, prompt, context),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.warning(
                "HostAgentWorker timed out",
                extra={"unit_id": str(unit_id), "agent_id": self._agent_id, "elapsed_ms": elapsed_ms},
            )
            raise WorkerTimeoutError(
                f"Host agent {self._agent_id!r} timed out after {timeout_seconds}s",
            ) from e
        except asyncio.CancelledError:
            # Propagate cancellation cleanly; surrounding executor handles it.
            raise
        except WorkerProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            elapsed_ms = int((time.time() - start) * 1000)
            logger.exception(
                "HostAgentWorker failed",
                extra={"unit_id": str(unit_id), "agent_id": self._agent_id, "elapsed_ms": elapsed_ms},
            )
            raise WorkerExecutionError(
                f"Host agent {self._agent_id!r} failed: {type(e).__name__}: {e}",
            ) from e

        elapsed_ms = int((time.time() - start) * 1000)

        if response_text is None:
            raise WorkerExecutionError(
                f"Host agent {self._agent_id!r} returned None",
            )
        text = response_text if isinstance(response_text, str) else str(response_text)

        return WorkerResult(
            provider=self.name,
            unit_id=unit_id,
            success=True,
            data={
                "content": text,
                "agent_id": self._agent_id,
            },
            confidence=self._default_confidence,
            artifacts=[],
            error=None,
            execution_time_ms=elapsed_ms,
            timestamp=datetime.now(timezone.utc),
        )

    def supports(self, task_type: str) -> bool:
        if not task_type:
            return False
        return task_type.lower() in self._supported

    @classmethod
    def _extract_prompt(cls, input_data: dict[str, Any]) -> str:
        for key in cls.DEFAULT_PROMPT_KEYS:
            value = input_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""


__all__ = [
    "HostAgentRunner",
    "HostAgentWorker",
]
