"""Agent Registry - Agent Factory V1.

Provides:
- Agent registration and management
- Default agent templates (CEO, Info, Dev, Review)
- Agent CRUD operations
- Tool permission validation
- Risk-level enforcement
- Approval-required action enforcement

Usage:
    registry = AgentRegistry()

    # Bootstrap default agents
    registry.bootstrap_defaults()

    # Get agent for routing
    agent = registry.get_agent_by_tags(["planning", "coordination"])

    # Check tool permissions
    result = registry.check_tool_permission(agent, "database_write", context)

    # CRUD operations
    agent = registry.create_agent(config)
    registry.enable_agent(agent_id)
    registry.disable_agent(agent_id)
"""

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    """Agent operational status."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class RiskLevel(str, Enum):
    """Risk level for agent operations."""

    LOW = "low"           # Read-only, no system changes
    MEDIUM = "medium"     # Minor changes, reversible
    HIGH = "high"         # Significant changes, requires approval
    CRITICAL = "critical" # System-level changes, requires multi-level approval


class ModelProvider(str, Enum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE = "azure"
    OLLAMA = "ollama"
    CUSTOM = "custom"


@dataclass
class Agent:
    """Agent definition."""

    agent_id: str
    name: str
    role: str              # e.g., "ceo", "info", "dev", "review"
    goal: str              # Agent's objective

    # Model configuration
    model_provider: ModelProvider
    model_name: str

    # Operational limits
    allowed_tools: list[str] = field(default_factory=list)
    max_subagents: int = 0
    timeout_seconds: int = 300
    retry_count: int = 3

    # Safety controls
    risk_level: RiskLevel = RiskLevel.MEDIUM
    approval_required_actions: list[str] = field(default_factory=list)

    # Routing
    routing_tags: list[str] = field(default_factory=list)

    # Rollout (V1.5)
    rollout_mode: str = "off"
    rollout_ratio: Optional[float] = None

    # Status
    status: AgentStatus = AgentStatus.ENABLED

    # Metadata
    version: str = "1.0"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "system"

    # Template reference
    template_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "goal": self.goal,
            "model_provider": self.model_provider.value if isinstance(self.model_provider, Enum) else self.model_provider,
            "model_name": self.model_name,
            "allowed_tools": self.allowed_tools,
            "max_subagents": self.max_subagents,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, Enum) else self.risk_level,
            "approval_required_actions": self.approval_required_actions,
            "routing_tags": self.routing_tags,
            "rollout_mode": self.rollout_mode,
            "rollout_ratio": self.rollout_ratio,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "template_id": self.template_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Agent":
        """Create Agent from dictionary."""
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            role=data["role"],
            goal=data["goal"],
            model_provider=ModelProvider(data["model_provider"]) if isinstance(data["model_provider"], str) else data["model_provider"],
            model_name=data["model_name"],
            allowed_tools=data.get("allowed_tools", []),
            max_subagents=data.get("max_subagents", 0),
            timeout_seconds=data.get("timeout_seconds", 300),
            retry_count=data.get("retry_count", 3),
            risk_level=RiskLevel(data["risk_level"]) if isinstance(data["risk_level"], str) else data["risk_level"],
            approval_required_actions=data.get("approval_required_actions", []),
            routing_tags=data.get("routing_tags", []),
            status=AgentStatus(data["status"]) if isinstance(data["status"], str) else data["status"],
            version=data.get("version", "1.0"),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data["created_at"], str) else data["created_at"],
            updated_at=datetime.fromisoformat(data["updated_at"]) if isinstance(data["updated_at"], str) else data["updated_at"],
            created_by=data.get("created_by", "system"),
            template_id=data.get("template_id"),
        )


@dataclass
class AgentTemplate:
    """Template for creating agents."""

    template_id: str
    name: str
    role: str
    goal: str
    model_provider: ModelProvider
    model_name: str
    allowed_tools: list[str]
    max_subagents: int
    timeout_seconds: int
    retry_count: int
    risk_level: RiskLevel
    approval_required_actions: list[str]
    routing_tags: list[str]
    version: str = "1.0"


# Default agent templates
CEO_AGENT_TEMPLATE = AgentTemplate(
    template_id="ceo",
    name="CEO Agent",
    role="ceo",
    goal="Coordinate all agents, make high-level decisions, delegate tasks to specialized agents. Ensure all work aligns with overall objectives.",
    model_provider=ModelProvider.OPENAI,
    model_name="gpt-4",
    allowed_tools=["task_create", "task_read", "task_update", "task_list", "agent_dispatch", "memory_read", "memory_write"],
    max_subagents=4,
    timeout_seconds=600,
    retry_count=3,
    risk_level=RiskLevel.HIGH,
    approval_required_actions=["policy:full_rollback", "tenant:delete", "config:global_change"],
    routing_tags=["planning", "coordination", "strategy", "决策", "规划"],
)

INFO_AGENT_TEMPLATE = AgentTemplate(
    template_id="info",
    name="Information Agent",
    role="info",
    goal="Gather, process, and provide information. Handle research tasks, data retrieval, and informational queries.",
    model_provider=ModelProvider.OPENAI,
    model_name="gpt-3.5-turbo",
    allowed_tools=["web_search", "web_fetch", "memory_read", "memory_write", "task_create", "task_read"],
    max_subagents=0,
    timeout_seconds=120,
    retry_count=2,
    risk_level=RiskLevel.LOW,
    approval_required_actions=[],
    routing_tags=["research", "information", "查询", "信息", "搜索"],
)

DEV_AGENT_TEMPLATE = AgentTemplate(
    template_id="dev",
    name="Developer Agent",
    role="dev",
    goal="Execute development tasks including code generation, debugging, testing, and implementation. Report progress and blockers.",
    model_provider=ModelProvider.OPENAI,
    model_name="gpt-4",
    allowed_tools=["code_generate", "code_execute", "code_test", "file_read", "file_write", "task_create", "task_update", "memory_read"],
    max_subagents=2,
    timeout_seconds=900,
    retry_count=3,
    risk_level=RiskLevel.MEDIUM,
    approval_required_actions=["dlq:bulk_discard", "plugin:install:global"],
    routing_tags=["development", "coding", "implementation", "开发", "代码"],
)

REVIEW_AGENT_TEMPLATE = AgentTemplate(
    template_id="review",
    name="Review Agent",
    role="review",
    goal="Review and validate outputs from other agents. Check quality, correctness, and completeness. Provide feedback.",
    model_provider=ModelProvider.OPENAI,
    model_name="gpt-4",
    allowed_tools=["code_review", "test_review", "task_read", "task_update", "memory_read", "comment_create"],
    max_subagents=0,
    timeout_seconds=300,
    retry_count=2,
    risk_level=RiskLevel.LOW,
    approval_required_actions=[],
    routing_tags=["review", "quality", "validation", "审核", "review", "质量"],
)

DEFAULT_TEMPLATES: dict[str, AgentTemplate] = {
    "ceo": CEO_AGENT_TEMPLATE,
    "info": INFO_AGENT_TEMPLATE,
    "dev": DEV_AGENT_TEMPLATE,
    "review": REVIEW_AGENT_TEMPLATE,
}


class AgentRegistry:
    """
    Registry for managing agents.

    Features:
    - Agent CRUD operations
    - Enable/disable agents
    - Clone from templates
    - Tool permission validation
    - Risk-level enforcement
    - Approval-required action checks
    """

    def __init__(self, db_path: str = "/tmp/agent_registry.db") -> None:
        """Initialize agent registry."""
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

        # In-memory cache
        self._agents: dict[str, Agent] = {}
        self._tags_index: dict[str, set[str]] = {}  # tag -> agent_ids

        self._init_db()
        self._load_agents()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                goal TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                allowed_tools TEXT,
                max_subagents INTEGER DEFAULT 0,
                timeout_seconds INTEGER DEFAULT 300,
                retry_count INTEGER DEFAULT 3,
                risk_level TEXT DEFAULT 'medium',
                approval_required_actions TEXT,
                routing_tags TEXT,
                status TEXT DEFAULT 'enabled',
                version TEXT DEFAULT '1.0',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT DEFAULT 'system',
                template_id TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agents_role
            ON agents(role)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agents_status
            ON agents(status)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agents_template
            ON agents(template_id)
        """)

        conn.commit()

    def _load_agents(self) -> None:
        """Load agents into memory cache."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM agents").fetchall()

        self._agents.clear()
        self._tags_index.clear()

        for row in rows:
            agent = self._row_to_agent(row)
            self._agents[agent.agent_id] = agent

            # Build tags index
            for tag in agent.routing_tags:
                if tag not in self._tags_index:
                    self._tags_index[tag] = set()
                self._tags_index[tag].add(agent.agent_id)

    def _row_to_agent(self, row: sqlite3.Row) -> Agent:
        """Convert row to Agent."""
        return Agent(
            agent_id=row["agent_id"],
            name=row["name"],
            role=row["role"],
            goal=row["goal"],
            model_provider=ModelProvider(row["model_provider"]),
            model_name=row["model_name"],
            allowed_tools=json.loads(row["allowed_tools"]) if row["allowed_tools"] else [],
            max_subagents=row["max_subagents"],
            timeout_seconds=row["timeout_seconds"],
            retry_count=row["retry_count"],
            risk_level=RiskLevel(row["risk_level"]),
            approval_required_actions=json.loads(row["approval_required_actions"]) if row["approval_required_actions"] else [],
            routing_tags=json.loads(row["routing_tags"]) if row["routing_tags"] else [],
            status=AgentStatus(row["status"]),
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            created_by=row["created_by"],
            template_id=row["template_id"],
        )

    def _agent_to_row(self, agent: Agent) -> dict[str, Any]:
        """Convert Agent to row dict."""
        return {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "role": agent.role,
            "goal": agent.goal,
            "model_provider": agent.model_provider.value,
            "model_name": agent.model_name,
            "allowed_tools": json.dumps(agent.allowed_tools),
            "max_subagents": agent.max_subagents,
            "timeout_seconds": agent.timeout_seconds,
            "retry_count": agent.retry_count,
            "risk_level": agent.risk_level.value,
            "approval_required_actions": json.dumps(agent.approval_required_actions),
            "routing_tags": json.dumps(agent.routing_tags),
            "status": agent.status.value,
            "version": agent.version,
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat(),
            "created_by": agent.created_by,
            "template_id": agent.template_id,
        }

    # ==================== CRUD Operations ====================

    def create_agent(
        self,
        name: str,
        role: str,
        goal: str,
        model_provider: ModelProvider,
        model_name: str,
        allowed_tools: Optional[list[str]] = None,
        max_subagents: int = 0,
        timeout_seconds: int = 300,
        retry_count: int = 3,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        approval_required_actions: Optional[list[str]] = None,
        routing_tags: Optional[list[str]] = None,
        created_by: str = "system",
    ) -> Agent:
        """Create a new agent."""
        agent_id = str(uuid.uuid4())[:8]

        agent = Agent(
            agent_id=agent_id,
            name=name,
            role=role,
            goal=goal,
            model_provider=model_provider,
            model_name=model_name,
            allowed_tools=allowed_tools or [],
            max_subagents=max_subagents,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
            risk_level=risk_level,
            approval_required_actions=approval_required_actions or [],
            routing_tags=routing_tags or [],
            status=AgentStatus.ENABLED,
            created_by=created_by,
        )

        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO agents
            (agent_id, name, role, goal, model_provider, model_name,
             allowed_tools, max_subagents, timeout_seconds, retry_count,
             risk_level, approval_required_actions, routing_tags, status,
             version, created_at, updated_at, created_by, template_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent.agent_id,
                agent.name,
                agent.role,
                agent.goal,
                agent.model_provider.value,
                agent.model_name,
                json.dumps(agent.allowed_tools),
                agent.max_subagents,
                agent.timeout_seconds,
                agent.retry_count,
                agent.risk_level.value,
                json.dumps(agent.approval_required_actions),
                json.dumps(agent.routing_tags),
                agent.status.value,
                agent.version,
                agent.created_at.isoformat(),
                agent.updated_at.isoformat(),
                agent.created_by,
                agent.template_id,
            ),
        )
        conn.commit()

        self._agents[agent.agent_id] = agent
        self._update_tags_index(agent)

        logger.info(f"Agent created: {agent.agent_id} ({agent.role})")

        return agent

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID."""
        return self._agents.get(agent_id)

    def list_agents(
        self,
        role: Optional[str] = None,
        status: Optional[AgentStatus] = None,
    ) -> list[Agent]:
        """List agents, optionally filtered."""
        agents = list(self._agents.values())

        if role:
            agents = [a for a in agents if a.role == role]

        if status:
            agents = [a for a in agents if a.status == status]

        return agents

    def update_agent(
        self,
        agent_id: str,
        name: Optional[str] = None,
        goal: Optional[str] = None,
        model_provider: Optional[ModelProvider] = None,
        model_name: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        max_subagents: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        retry_count: Optional[int] = None,
        risk_level: Optional[RiskLevel] = None,
        approval_required_actions: Optional[list[str]] = None,
        routing_tags: Optional[list[str]] = None,
        rollout_mode: Optional[str] = None,
        rollout_ratio: Optional[float] = None,
    ) -> Optional[Agent]:
        """Update an agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        # Update fields
        if name is not None:
            agent.name = name
        if goal is not None:
            agent.goal = goal
        if model_provider is not None:
            agent.model_provider = model_provider
        if model_name is not None:
            agent.model_name = model_name
        if allowed_tools is not None:
            agent.allowed_tools = allowed_tools
        if max_subagents is not None:
            agent.max_subagents = max_subagents
        if timeout_seconds is not None:
            agent.timeout_seconds = timeout_seconds
        if retry_count is not None:
            agent.retry_count = retry_count
        if risk_level is not None:
            agent.risk_level = risk_level
        if approval_required_actions is not None:
            agent.approval_required_actions = approval_required_actions
        if routing_tags is not None:
            agent.routing_tags = routing_tags
        if rollout_mode is not None:
            agent.rollout_mode = rollout_mode
        if rollout_ratio is not None:
            agent.rollout_ratio = rollout_ratio

        agent.updated_at = datetime.now(timezone.utc)

        conn = self._get_conn()
        conn.execute(
            """
            UPDATE agents SET
                name = ?, goal = ?, model_provider = ?, model_name = ?,
                allowed_tools = ?, max_subagents = ?, timeout_seconds = ?,
                retry_count = ?, risk_level = ?, approval_required_actions = ?,
                routing_tags = ?, updated_at = ?
            WHERE agent_id = ?
            """,
            (
                agent.name,
                agent.goal,
                agent.model_provider.value,
                agent.model_name,
                json.dumps(agent.allowed_tools),
                agent.max_subagents,
                agent.timeout_seconds,
                agent.retry_count,
                agent.risk_level.value,
                json.dumps(agent.approval_required_actions),
                json.dumps(agent.routing_tags),
                agent.updated_at.isoformat(),
                agent_id,
            ),
        )
        conn.commit()

        # Rebuild tags index
        self._rebuild_tags_index()
        self._agents[agent_id] = agent

        logger.info(f"Agent updated: {agent_id}")

        return agent

    def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent."""
        if agent_id not in self._agents:
            return False

        conn = self._get_conn()
        conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        conn.commit()

        del self._agents[agent_id]
        self._rebuild_tags_index()

        logger.info(f"Agent deleted: {agent_id}")

        return True

    # ==================== Enable/Disable ====================

    def enable_agent(self, agent_id: str) -> Optional[Agent]:
        """Enable an agent."""
        return self._set_agent_status(agent_id, AgentStatus.ENABLED)

    def disable_agent(self, agent_id: str) -> Optional[Agent]:
        """Disable an agent."""
        return self._set_agent_status(agent_id, AgentStatus.DISABLED)

    def _set_agent_status(
        self, agent_id: str, status: AgentStatus
    ) -> Optional[Agent]:
        """Set agent status."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        agent.status = status
        agent.updated_at = datetime.now(timezone.utc)

        conn = self._get_conn()
        conn.execute(
            "UPDATE agents SET status = ?, updated_at = ? WHERE agent_id = ?",
            (status.value, agent.updated_at.isoformat(), agent_id),
        )
        conn.commit()

        logger.info(f"Agent {status.value}: {agent_id}")

        return agent

    # ==================== Clone from Template ====================

    def clone_from_template(
        self,
        template_id: str,
        name: Optional[str] = None,
        created_by: str = "system",
    ) -> Optional[Agent]:
        """Clone an agent from a template."""
        template = DEFAULT_TEMPLATES.get(template_id)
        if not template:
            return None

        # Check if template already exists for this tenant
        existing = [
            a for a in self._agents.values()
            if a.template_id == template_id and a.created_by == created_by
        ]
        if existing:
            return existing[0]

        agent_id = str(uuid.uuid4())[:8]

        agent = Agent(
            agent_id=agent_id,
            name=name or template.name,
            role=template.role,
            goal=template.goal,
            model_provider=template.model_provider,
            model_name=template.model_name,
            allowed_tools=template.allowed_tools.copy(),
            max_subagents=template.max_subagents,
            timeout_seconds=template.timeout_seconds,
            retry_count=template.retry_count,
            risk_level=template.risk_level,
            approval_required_actions=template.approval_required_actions.copy(),
            routing_tags=template.routing_tags.copy(),
            status=AgentStatus.ENABLED,
            created_by=created_by,
            template_id=template_id,
        )

        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO agents
            (agent_id, name, role, goal, model_provider, model_name,
             allowed_tools, max_subagents, timeout_seconds, retry_count,
             risk_level, approval_required_actions, routing_tags, status,
             version, created_at, updated_at, created_by, template_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent.agent_id,
                agent.name,
                agent.role,
                agent.goal,
                agent.model_provider.value,
                agent.model_name,
                json.dumps(agent.allowed_tools),
                agent.max_subagents,
                agent.timeout_seconds,
                agent.retry_count,
                agent.risk_level.value,
                json.dumps(agent.approval_required_actions),
                json.dumps(agent.routing_tags),
                agent.status.value,
                agent.version,
                agent.created_at.isoformat(),
                agent.updated_at.isoformat(),
                agent.created_by,
                agent.template_id,
            ),
        )
        conn.commit()

        self._agents[agent.agent_id] = agent
        self._update_tags_index(agent)

        logger.info(f"Agent cloned from template {template_id}: {agent.agent_id}")

        return agent

    # ==================== Bootstrap Defaults ====================

    def bootstrap_defaults(self, created_by: str = "system") -> list[Agent]:
        """
        Bootstrap default agent templates.

        Idempotent: If agent from template already exists, returns existing.
        """
        agents = []
        for template_id in DEFAULT_TEMPLATES:
            agent = self.clone_from_template(template_id, created_by=created_by)
            if agent:
                agents.append(agent)
        return agents

    # ==================== Routing ====================

    def get_agent_by_tags(self, tags: list[str]) -> Optional[Agent]:
        """
        Get the best matching agent for given tags.

        Uses tag matching with priority:
        1. Exact tag match on enabled agents
        2. Return first match found
        """
        if not tags:
            return None

        # Find agents that have any of the matching tags
        candidate_ids: set[str] = set()
        for tag in tags:
            if tag in self._tags_index:
                candidate_ids.update(self._tags_index[tag])

        if not candidate_ids:
            return None

        # Filter to enabled agents and return one
        enabled = [
            self._agents[aid] for aid in candidate_ids
            if aid in self._agents and self._agents[aid].status == AgentStatus.ENABLED
        ]

        if not enabled:
            return None

        # Return first enabled match (could be enhanced with scoring)
        return enabled[0]

    def get_agent_by_role(self, role: str) -> Optional[Agent]:
        """Get enabled agent by role."""
        agents = self.list_agents(role=role, status=AgentStatus.ENABLED)
        return agents[0] if agents else None

    def _update_tags_index(self, agent: Agent) -> None:
        """Update tags index for an agent."""
        for tag in agent.routing_tags:
            if tag not in self._tags_index:
                self._tags_index[tag] = set()
            self._tags_index[tag].add(agent.agent_id)

    def _rebuild_tags_index(self) -> None:
        """Rebuild the entire tags index."""
        self._tags_index.clear()
        for agent in self._agents.values():
            for tag in agent.routing_tags:
                if tag not in self._tags_index:
                    self._tags_index[tag] = set()
                self._tags_index[tag].add(agent.agent_id)

    # ==================== Tool Permission Validation ====================

    def check_tool_permission(
        self,
        agent_id: str,
        tool_name: str,
        action: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if agent is allowed to use a tool.

        Returns:
            (allowed, error_code)
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return False, "AGENT_NOT_FOUND"

        if agent.status != AgentStatus.ENABLED:
            return False, "AGENT_DISABLED"

        if tool_name not in agent.allowed_tools:
            return False, "TOOL_NOT_ALLOWED"

        # Check if action requires approval
        if action and action in agent.approval_required_actions:
            return False, "APPROVAL_REQUIRED"

        return True, None

    def get_agent_execution_config(
        self, agent_id: str
    ) -> Optional[dict[str, Any]]:
        """Get execution configuration for agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        return {
            "agent_id": agent.agent_id,
            "timeout_seconds": agent.timeout_seconds,
            "retry_count": agent.retry_count,
            "max_subagents": agent.max_subagents,
            "allowed_tools": agent.allowed_tools,
            "risk_level": agent.risk_level.value,
        }


# Global registry instance
_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """Get global agent registry."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


def init_agent_registry(db_path: str) -> AgentRegistry:
    """Initialize global agent registry with custom db path."""
    global _registry
    _registry = AgentRegistry(db_path=db_path)
    return _registry
