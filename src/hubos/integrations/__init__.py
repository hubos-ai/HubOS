"""Host-side adapters that bridge this application to embedded libraries.

Currently exposes a ``hubos.core.workers.providers.host_agent.HostAgentRunner``
backed by this app's ``MultiAgentManager`` / ``Workspace`` runtime, so that the
hubos.core Coordinator can dispatch tasks to the same agents the WebUI talks to.
"""

from .host_agent_runner import (
    DEFAULT_CHANNEL,
    DEFAULT_USER_ID,
    build_host_agent_runner,
)

__all__ = [
    "build_host_agent_runner",
    "DEFAULT_CHANNEL",
    "DEFAULT_USER_ID",
]
