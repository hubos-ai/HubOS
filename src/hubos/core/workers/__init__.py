# -*- coding: utf-8 -*-
"""Workers module for solo-hub."""

from hubos.core.workers.providers.base import (
    WorkerExecutionError,
    WorkerProvider,
    WorkerProviderError,
    WorkerResult,
    WorkerTimeoutError,
)
from hubos.core.workers.providers.executable import ExecutableWorkerProvider
from hubos.core.workers.providers.host_agent import (
    HostAgentRunner,
    HostAgentWorker,
)
from hubos.core.workers.providers.stub import StubWorkerProvider
from hubos.core.workers.registry import (
    clear_host_agent_runner,
    get_host_agent_runner,
    set_host_agent_runner,
)

__all__ = [
    "WorkerProvider",
    "WorkerProviderError",
    "WorkerExecutionError",
    "WorkerTimeoutError",
    "WorkerResult",
    "StubWorkerProvider",
    "ExecutableWorkerProvider",
    "HostAgentWorker",
    "HostAgentRunner",
    "set_host_agent_runner",
    "get_host_agent_runner",
    "clear_host_agent_runner",
]
