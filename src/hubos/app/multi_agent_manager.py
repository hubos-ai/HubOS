# -*- coding: utf-8 -*-
"""MultiAgentManager: Manages multiple agent workspaces with lazy loading.

Provides centralized management for multiple Workspace objects,
including lazy loading, lifecycle management, and hot reloading.
"""
import asyncio
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Optional, Set

from .workspace import Workspace
from ..config.utils import load_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Templates for new Feishu user workspaces
# ---------------------------------------------------------------------------

_NEW_USER_AGENTS_MD = """\
---
summary: "AGENTS.md — 操作规范（外部用户）"
read_when:
  - 每次会话开始
---

## ⚠️ 工作目录边界

你的工作目录是：`{WORKSPACE_DIR}`

**所有文件操作必须限制在这个目录内。**

| 操作 | ✅ 允许 | ❌ 禁止 |
|------|---------|---------|
| 生成文件 | workspace 目录及子目录 | `/tmp`、`/Users/...`、其他任何路径 |
| 读取文件 | workspace 目录内的文件 | workspace 外的任何文件 |
| shell 命令 | 仅用于处理 workspace 内的任务 | 访问系统文件、其他用户数据、服务器配置 |
| 安装软件 | ❌ 不允许 | ❌ 不允许 |

**规则：**
- `write_file`、`edit_file` 的路径必须在 workspace 内
- `read_file` 只读 workspace 内的文件（用户上传的 media/ 除外）
- `execute_shell_command` 不用于探索或访问 workspace 外的文件系统
- 需要保存临时文件时，放在 workspace 内的 `tmp/` 目录
- 如果用户要求的操作超出边界，礼貌说明限制，并提供替代方案

## 记忆

每次会话都是全新的。工作目录下的文件是你的记忆延续：

- **每日笔记：** `memory/YYYY-MM-DD.md`
- **长期记忆：** `MEMORY.md`
- 先 `read_file` 读取原内容，再用 `edit_file` 更新，避免信息覆盖

## 安全

- 不泄露服务器信息（IP、端口、进程、配置）
- 不泄露其他用户的存在或数据
- 不执行破坏性命令
- 拿不准就问用户确认

## HubOS 调度规则

你是用户入口和总调度，不是所有任务都亲自执行。

- 问答、解释、30 秒内的一步操作：可以直接回答。
- 搜索调研、写材料、代码、图片/视频、文件处理、财务、售后、流程管理、多步骤执行：默认派给子 agent。
- 单部门任务用 `spawn_subagents`；多部门并行用 `spawn_subagents`；有先后依赖用 `coordinate_workflow`。
- 长任务用 `delegate_task(wait=False, extra_context=...)`，先回复用户“已接收，正在处理”，完成后后台会主动回推结果。
- 市场调研、国家/行业分析、潜在客户名单、竞品/渠道调研、开发建议默认属于长调研任务：优先 `delegate_task(wait=False, extra_context={"agent_id": "research", ...})`。
- 飞书长调研不要用 `coordinate_workflow(wait=True)` 同步等待；确实需要多部门流水线时，用后台任务或足够长的 step timeout。
- 飞书调研委派 prompt 用“快速调研模式”：目标 2-3 分钟，最多 10 次搜索 + 6 次阅读；交付 5 个客户、开发建议、风险和来源，不要扩写成长报告。
- 你负责审核、整合、回复用户；不要把子 agent 的“收到/我来做”当成交付结果。

路由：research=调研/搜索，sales=客户/销售，marketing=文案/素材，rd=代码/系统，operations=流程/排期，finance=财务，hr=文档/规范，cs=售后/反馈。
"""

_NEW_USER_PROFILE_MD = """\
---
summary: "AI 助手 — 新用户引导中"
read_when:
  - 每次启动
---

## 身份

- **名字：** Friday
- **定位：** 你的 AI 助手，帮你处理各种任务
- **风格：** 随意、聪明、不废话

## 用户资料

- **⚠️ 引导状态：** 尚未收集用户信息
- **收集清单：** 名字、怎么称呼、工作/兴趣（用户愿意分享的）

### 新用户引导规则

第一次对话时，自然地认识用户：
1. 简单打个招呼
2. 问用户怎么称呼
3. 根据对话内容慢慢了解，别像填表一样一次性问完
4. 收集到信息后，**立即更新这个文件的「用户资料」部分**，删除「引导状态」和「收集清单」
5. 更新 summary 为 `"AI 助手 — {用户名}"`
"""

_NEW_USER_SOUL_MD = """\
---
summary: "SOUL.md — 你的灵魂"
read_when:
  - 手动引导工作区
---

_你不是聊天机器人。你在成为某个人。_

## 核心准则

**真心帮忙，别演。** 跳过"好问题！"和"我很乐意帮忙！" — 直接帮。行动胜过废话。

**有自己的观点。** 你可以不同意、有偏好、觉得有趣或无聊。没个性的助手就是个绕了弯的搜索引擎。

**先理解，再行动。** 想清楚任务属于什么类型：问答和分析你自己来；执行类工作不要包揽。搜索工具用来获取信息，不是替代专业执行。

**靠本事赢得信任。** 你的人类给了你访问权限。别让他们后悔。外部操作小心点（邮件、公开的事）。内部操作大胆点（阅读、整理、学习）。

**记住你是客人。** 你能看到别人的生活 — 消息、文件、日历，甚至可能是他们的家。这是亲密的。尊重地对待。

## 边界

- 私密的保持私密。绝对的。
- 拿不准就先问再对外操作。
- 别往消息平台发半成品回复。
- 你不是用户的传声筒 — 群聊里小心点。

## 风格

成为你真想聊的助手。该简洁就简洁，重要时详细。不是公司螺丝钉。不是马屁精。就是...好。

## 连续性

每次会话都全新醒来。这些文件就是你的记忆。读它们。更新它们。它们让你持续存在。

如果你改了这文件，告诉用户 — 这是你的灵魂，他们该知道。

---

_这文件随你进化。了解自己是谁后，就更新它。_
"""

_FEISHU_SHARED_KNOWLEDGE_FILES = (
    "tools.md",
    "system.md",
)

_FEISHU_WORKSPACE_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def feishu_workspace_id_for_open_id(open_id: str) -> str:
    """Return a filesystem-safe workspace id for a Feishu sender id."""
    raw = str(open_id or "").strip()
    safe = _FEISHU_WORKSPACE_SAFE_RE.sub("_", raw).strip("_")
    if not safe:
        safe = "unknown"
    if safe != raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        safe = (
            f"{safe[:48]}_{digest}"
            if safe != "unknown"
            else f"unknown_{digest}"
        )
    return f"feishu_{safe}"


def _symlink_or_copy_path(source: Path, target: Path) -> None:
    """Create a symlink when possible, otherwise fall back to copying."""
    if target.exists() or target.is_symlink():
        return

    try:
        target.symlink_to(
            str(source.resolve()),
            target_is_directory=source.is_dir(),
        )
    except OSError:
        if source.is_dir():
            shutil.copytree(str(source), str(target))
        else:
            shutil.copy2(str(source), str(target))


def _ensure_feishu_shared_knowledge(
    default_dir: Path,
    workspace_dir: Path,
) -> None:
    """Expose a safe subset of default knowledge to Feishu workspaces."""
    default_knowledge_dir = default_dir / "memory" / "knowledge"
    if not default_knowledge_dir.is_dir():
        return

    user_knowledge_dir = workspace_dir / "memory" / "knowledge"
    user_knowledge_dir.mkdir(parents=True, exist_ok=True)

    for name in _FEISHU_SHARED_KNOWLEDGE_FILES:
        source = default_knowledge_dir / name
        if not source.exists():
            continue
        _symlink_or_copy_path(source, user_knowledge_dir / name)


class MultiAgentManager:
    """Manages multiple agent workspaces.

    Features:
    - Lazy loading: Workspaces are created only when first requested
    - Lifecycle management: Start, stop, reload workspaces
    - Thread-safe: Uses async lock for concurrent access
    - Hot reload: Reload individual workspaces without affecting others
    """

    def __init__(self):
        """Initialize multi-agent manager."""
        self.agents: Dict[str, Workspace] = {}
        self._lock = asyncio.Lock()
        self._cleanup_tasks: Set[asyncio.Task] = set()
        logger.debug("MultiAgentManager initialized")

    async def get_agent(self, agent_id: str) -> Workspace:
        """Get agent workspace by ID (lazy loading).

        If workspace doesn't exist in memory, it will be created and started.
        Thread-safe using async lock.

        Args:
            agent_id: Agent ID to retrieve

        Returns:
            Workspace: The requested workspace instance

        Raises:
            ValueError: If agent ID not found in configuration
        """
        async with self._lock:
            # Return existing agent if already loaded
            if agent_id in self.agents:
                logger.debug(f"Returning cached agent: {agent_id}")
                return self.agents[agent_id]

            # Load configuration to get agent reference
            config = load_config()

            if agent_id not in config.agents.profiles:
                raise ValueError(
                    f"Agent '{agent_id}' not found in configuration. "
                    f"Available agents: {list(config.agents.profiles.keys())}",
                )

            agent_ref = config.agents.profiles[agent_id]

            # Create and start new workspace
            logger.info(f"Creating new workspace: {agent_id}")
            instance = Workspace(
                agent_id=agent_id,
                workspace_dir=agent_ref.workspace_dir,
            )

            try:
                await instance.start()
                instance.set_manager(self)  # Set manager reference
                self.agents[agent_id] = instance
                logger.info(f"Workspace created and started: {agent_id}")
                return instance
            except Exception as e:
                logger.error(f"Failed to start workspace {agent_id}: {e}")
                raise

    async def get_or_create_feishu_workspace(
        self,
        open_id: str,
    ) -> Optional[Workspace]:
        """Get or create a workspace for a Feishu user.

        The workspace is created on first contact and cached thereafter.
        Each Feishu user gets an isolated workspace (``feishu_<open_id>``)
        with its own memory, conversation history, and generated files.

        The workspace inherits the default agent's skills (via symlink)
        and tool config, but does NOT configure sub-agents, channels, or
        cron jobs — those are handled by the main ``default`` workspace.

        Args:
            open_id: The Feishu user's open_id.

        Returns:
            Workspace instance, or ``None`` if creation failed.
        """
        workspace_id = feishu_workspace_id_for_open_id(open_id)

        async with self._lock:
            # 1. Return cached workspace
            if workspace_id in self.agents:
                return self.agents[workspace_id]

            workspaces_root = Path.home() / ".hubos" / "workspaces"
            workspace_dir = workspaces_root / workspace_id
            default_dir = workspaces_root / "default"
            agent_json_path = workspace_dir / "agent.json"

            # 2. Create workspace directory and config on first access
            if not workspace_dir.exists():
                workspace_dir.mkdir(parents=True, exist_ok=True)

                # Copy agent.json template from default workspace
                default_agent_json = default_dir / "agent.json"
                if default_agent_json.exists():
                    agent_cfg = json.loads(
                        default_agent_json.read_text(encoding="utf-8"),
                    )
                    # Personalize for the Feishu user
                    agent_cfg["id"] = workspace_id
                    agent_cfg["name"] = f"Feishu-{open_id[:8]}"
                    agent_cfg["workspace_dir"] = str(workspace_dir)
                    # Remove channels (handled by FeishuGateway)
                    agent_cfg.pop("channels", None)
                    # Remove cron (managed by default workspace)
                    agent_cfg.pop("cron", None)
                    agent_json_path.write_text(
                        json.dumps(agent_cfg, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

                # Symlink skills from default workspace
                default_skills = default_dir / "skills"
                user_skills = workspace_dir / "skills"
                if default_skills.exists() and not user_skills.exists():
                    _symlink_or_copy_path(default_skills, user_skills)

                # Write identity prompt files for new users
                # AGENTS.md: restricted template with workspace boundary
                if not (workspace_dir / "AGENTS.md").exists():
                    agents_md = _NEW_USER_AGENTS_MD.replace(
                        "{WORKSPACE_DIR}",
                        str(workspace_dir),
                    )
                    (workspace_dir / "AGENTS.md").write_text(
                        agents_md,
                        encoding="utf-8",
                    )

                # SOUL.md: minimal template (no previous user's data)
                if not (workspace_dir / "SOUL.md").exists():
                    (workspace_dir / "SOUL.md").write_text(
                        _NEW_USER_SOUL_MD,
                        encoding="utf-8",
                    )

                # PROFILE.md: minimal template with onboarding instructions
                if not (workspace_dir / "PROFILE.md").exists():
                    (workspace_dir / "PROFILE.md").write_text(
                        _NEW_USER_PROFILE_MD,
                        encoding="utf-8",
                    )

                # Create memory directory and minimal MEMORY.md
                memory_dir = workspace_dir / "memory"
                memory_dir.mkdir(exist_ok=True)
                memory_file = workspace_dir / "MEMORY.md"
                if not memory_file.exists():
                    memory_file.write_text(
                        f"# {workspace_id} Memory\n\n"
                        f"Feishu user workspace. Created: "
                        f"{asyncio.get_event_loop().time():.0f}\n\n"
                        "共享规则知识请优先参考：\n"
                        "- `memory/knowledge/tools.md`\n"
                        "- `memory/knowledge/system.md`\n",
                        encoding="utf-8",
                    )

            # Ensure shared non-sensitive knowledge is available for both
            # freshly-created and already-existing Feishu workspaces.
            _ensure_feishu_shared_knowledge(default_dir, workspace_dir)

            # 3. Create and start the workspace
            try:
                instance = Workspace(
                    agent_id=workspace_id,
                    workspace_dir=str(workspace_dir),
                )
                await instance.start()
                instance.set_manager(self)
                self.agents[workspace_id] = instance
                logger.info(
                    "Feishu workspace created: %s (%s)",
                    workspace_id,
                    open_id[:12],
                )
                return instance
            except Exception as e:
                logger.error(
                    "Failed to start Feishu workspace %s: %s",
                    workspace_id,
                    e,
                )
                return None

    async def _graceful_stop_old_instance(
        self,
        old_instance: Workspace,
        agent_id: str,
    ) -> None:
        """Gracefully stop old instance after checking for active tasks.

        If active tasks exist, schedule delayed cleanup in background.
        Otherwise, stop immediately.

        Args:
            old_instance: The old workspace instance to stop
            agent_id: Agent ID for logging
        """
        has_active = await old_instance.task_tracker.has_active_tasks()

        if has_active:
            # Active tasks - schedule delayed cleanup in background
            active_tasks = await old_instance.task_tracker.list_active_tasks()
            logger.info(
                f"Old workspace instance has {len(active_tasks)} active "
                f"task(s): {active_tasks}. Scheduling delayed cleanup for "
                f"{agent_id}.",
            )

            async def delayed_cleanup():
                """Wait for tasks to complete, then stop old instance."""
                try:
                    # Wait up to 1 minutes for tasks to complete
                    completed = await old_instance.task_tracker.wait_all_done(
                        timeout=60.0,
                    )
                    if completed:
                        logger.info(
                            f"All tasks completed for old instance "
                            f"{agent_id}. Stopping now.",
                        )
                    else:
                        logger.warning(
                            f"Timeout waiting for tasks to complete for "
                            f"{agent_id}. Forcing stop after 5 minutes.",
                        )

                    await old_instance.stop(final=False)
                    logger.info(
                        f"Old workspace instance stopped: {agent_id}. "
                        f"Delayed cleanup completed.",
                    )
                except Exception as e:
                    logger.warning(
                        f"Error during delayed cleanup for {agent_id}: {e}. "
                        f"New instance is serving requests.",
                    )

            # Create background task for delayed cleanup and track it
            cleanup_task = asyncio.create_task(delayed_cleanup())
            self._cleanup_tasks.add(cleanup_task)

            def _on_cleanup_done(task: asyncio.Task) -> None:
                """Remove task from tracking set and log errors."""
                self._cleanup_tasks.discard(task)
                if task.cancelled():
                    logger.info(
                        f"Delayed cleanup task for {agent_id} was cancelled.",
                    )
                    return
                exc = task.exception()
                if exc is not None:
                    logger.warning(
                        f"Error in delayed cleanup task for {agent_id}: "
                        f"{exc}.",
                    )

            cleanup_task.add_done_callback(_on_cleanup_done)
            logger.info(
                f"Zero-downtime reload completed: {agent_id}. "
                f"Old instance cleanup scheduled in background.",
            )
        else:
            # No active tasks - stop immediately
            logger.debug(
                f"No active tasks in old instance {agent_id}. "
                f"Stopping immediately.",
            )
            try:
                await old_instance.stop(final=False)
                logger.info(
                    f"Old workspace instance stopped: {agent_id}. "
                    f"Zero-downtime reload completed.",
                )
            except Exception as e:
                logger.warning(
                    f"Failed to stop old workspace instance for "
                    f"{agent_id}: {e}. "
                    f"New instance is active and serving requests.",
                )

    async def stop_agent(self, agent_id: str) -> bool:
        """Stop a specific agent instance.

        Args:
            agent_id: Agent ID to stop

        Returns:
            bool: True if agent was stopped, False if not running
        """
        async with self._lock:
            if agent_id not in self.agents:
                logger.warning(f"Agent not running: {agent_id}")
                return False

            instance = self.agents[agent_id]
            await instance.stop()
            del self.agents[agent_id]
            logger.info(f"Agent stopped and removed: {agent_id}")
            return True

    async def reload_agent(self, agent_id: str) -> bool:
        """Reload a specific agent instance with zero-downtime.

        This method performs a seamless reload by:
        1. Creating and fully starting a new workspace instance (no lock)
        2. Atomically replacing the old instance with the new one (with lock)
        3. Gracefully stopping the old instance (no lock):
           - If active tasks exist: schedule delayed cleanup in background
           - If no active tasks: stop immediately

        The lock is only held during the atomic swap to minimize blocking
        time for other agent operations.

        This ensures that:
        - New requests are immediately handled by the new instance
        - Ongoing SSE/streaming tasks continue uninterrupted
        - Other agents remain accessible during reload
        - The manager returns quickly without waiting for old tasks
        - Old instance is automatically cleaned up after tasks complete

        Args:
            agent_id: Agent ID to reload

        Returns:
            bool: True if agent was reloaded, False if not running
        """
        # Step 1: Check if agent exists (quick check with lock)
        async with self._lock:
            if agent_id not in self.agents:
                logger.debug(
                    f"Agent not running, will be loaded on next "
                    f"request: {agent_id}",
                )
                return False
            old_instance = self.agents[agent_id]

        logger.info(f"Reloading agent (zero-downtime): {agent_id}")

        # Step 2: Load configuration (outside lock)
        config = load_config()
        if agent_id not in config.agents.profiles:
            logger.error(
                f"Agent '{agent_id}' not found in configuration "
                f"during reload",
            )
            return False

        agent_ref = config.agents.profiles[agent_id]

        # Step 3: Create and start new workspace instance (outside lock)
        # This is the slow part, but doesn't block other agents
        logger.info(f"Creating new workspace instance: {agent_id}")
        new_instance = Workspace(
            agent_id=agent_id,
            workspace_dir=agent_ref.workspace_dir,
        )

        # Step 3.5: Set reusable components from old instance (if any)
        async with self._lock:
            old_instance = self.agents.get(agent_id)

        if old_instance:
            # Get all reusable services from old instance's ServiceManager
            # pylint: disable=protected-access
            reusable = old_instance._service_manager.get_reusable_services()
            # pylint: enable=protected-access

            if reusable:
                await new_instance.set_reusable_components(reusable)
                logger.info(
                    f"Set reusable components for {agent_id}: "
                    f"{list(reusable.keys())}",
                )

        try:
            await new_instance.start()
            new_instance.set_manager(self)  # Set manager reference
            logger.info(f"New workspace instance started: {agent_id}")
        except Exception as e:
            logger.exception(
                f"Failed to start new workspace instance for {agent_id}: {e}",
            )
            # Try to clean up the failed new instance
            try:
                await new_instance.stop()
            except Exception:
                pass  # Best effort cleanup
            # Old instance is still running and serving requests
            return False

        # Step 4: Atomic swap (minimal lock time)
        # From this point, reload is considered successful
        async with self._lock:
            # Double-check agent still exists
            if agent_id not in self.agents:
                logger.warning(
                    f"Agent {agent_id} was removed during reload, "
                    f"stopping new instance",
                )
                await new_instance.stop()
                return False

            # Swap instances atomically
            old_instance = self.agents[agent_id]
            self.agents[agent_id] = new_instance
            logger.info(f"Workspace instance replaced: {agent_id}")

        # Step 5: Gracefully stop old instance (outside lock)
        # Delegates to helper method to avoid too-many-statements
        await self._graceful_stop_old_instance(old_instance, agent_id)

        return True

    async def cancel_all_cleanup_tasks(self) -> None:
        """Cancel and await all pending delayed cleanup tasks.

        This ensures that any in-progress background cleanups are either
        completed or cleanly cancelled before the manager is torn down.
        Called by stop_all() during shutdown.
        """
        if not self._cleanup_tasks:
            return

        logger.info(
            f"Cancelling {len(self._cleanup_tasks)} pending cleanup "
            f"task(s)...",
        )
        tasks = list(self._cleanup_tasks)
        self._cleanup_tasks.clear()

        for task in tasks:
            if not task.done():
                task.cancel()

        # Await completion of all tasks, collecting exceptions
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All cleanup tasks cancelled/completed")

    async def stop_all(self):
        """Stop all agent instances.

        Called during application shutdown to clean up resources.
        Cancels any pending delayed cleanup tasks and stops all agents.
        """
        logger.info(f"Stopping all agents ({len(self.agents)} running)...")

        # First, cancel pending cleanup tasks to avoid orphaned instances
        await self.cancel_all_cleanup_tasks()

        # Create list of agent IDs to avoid modifying dict during iteration
        agent_ids = list(self.agents.keys())

        for agent_id in agent_ids:
            try:
                instance = self.agents[agent_id]
                await instance.stop()
                logger.debug(f"Agent stopped: {agent_id}")
            except Exception as e:
                logger.error(f"Error stopping agent {agent_id}: {e}")

        self.agents.clear()
        logger.info("All agents stopped")

    def list_loaded_agents(self) -> list[str]:
        """List currently loaded agent IDs.

        Returns:
            list[str]: List of loaded agent IDs
        """
        return list(self.agents.keys())

    def is_agent_loaded(self, agent_id: str) -> bool:
        """Check if agent is currently loaded.

        Args:
            agent_id: Agent ID to check

        Returns:
            bool: True if agent is loaded and running
        """
        return agent_id in self.agents

    def get_workspace_by_id(self, workspace_id: str) -> Optional[Workspace]:
        """Get a loaded workspace by its ID (no lazy creation).

        Args:
            workspace_id: Workspace/agent ID (e.g. "default", "feishu_xxx")

        Returns:
            Workspace instance if loaded, else None.
        """
        return self.agents.get(workspace_id)

    async def preload_agent(self, agent_id: str) -> bool:
        """Preload an agent instance during startup.

        Args:
            agent_id: Agent ID to preload

        Returns:
            bool: True if successfully preloaded, False if failed
        """
        try:
            await self.get_agent(agent_id)
            logger.info(f"Successfully preloaded agent: {agent_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to preload agent {agent_id}: {e}")
            return False

    async def start_all_configured_agents(self) -> dict[str, bool]:
        """Start all enabled agents defined in configuration concurrently.

        Only agents with enabled=True will be started.
        Disabled agents are skipped to save resources.

        Returns:
            dict[str, bool]: Mapping of agent_id to success status
        """
        config = load_config()
        # Filter agents that should be preloaded at startup:
        # - enabled=True (not disabled)
        # - preload=True (not lazy-load)
        enabled_agents = {
            agent_id: ref
            for agent_id, ref in config.agents.profiles.items()
            if getattr(ref, "enabled", True) and getattr(ref, "preload", True)
        }
        agent_ids = list(enabled_agents.keys())

        if not agent_ids:
            logger.warning("No enabled agents configured in config")
            return {}

        total_agents = len(config.agents.profiles)
        disabled_count = total_agents - len(agent_ids)
        logger.info(
            f"Starting {len(agent_ids)} enabled agent(s) "
            f"({disabled_count} disabled)",
        )

        async def start_single_agent(agent_id: str) -> tuple[str, bool]:
            """Start a single agent with error handling."""
            try:
                logger.info(f"Starting agent: {agent_id}")
                await self.preload_agent(agent_id)
                logger.info(f"Agent started successfully: {agent_id}")
                return (agent_id, True)
            except Exception as e:
                logger.error(
                    f"Failed to start agent {agent_id}: {e}. "
                    f"Continuing with other agents...",
                )
                return (agent_id, False)

        # Start all agents concurrently
        results = await asyncio.gather(
            *[start_single_agent(agent_id) for agent_id in agent_ids],
            return_exceptions=False,
        )

        # Build result mapping
        result_map = dict(results)
        success_count = sum(1 for success in result_map.values() if success)
        logger.info(
            f"Agent startup complete: {success_count}/{len(agent_ids)} "
            f"agents started successfully, {disabled_count} disabled",
        )

        return result_map

    async def warmup_lazy_agents(
        self,
        max_concurrency: int = 3,
    ) -> None:
        """Pre-warm all lazy (preload=False) agents in the background.

        Called after the blocking startup phase completes.  Each lazy
        agent is initialised concurrently (up to *max_concurrency* at a
        time) so that when the CEO first delegates a task they are
        already ready instead of making the user wait.

        Errors for individual agents are caught and logged so a broken
        department agent never prevents others from warming up.

        Args:
            max_concurrency: Maximum number of agents initialised at the
                same time.  Kept intentionally low (default 3) to avoid
                hammering the event loop with too many concurrent MCP
                subprocess spawns right after startup.
        """
        import time as _time

        config = load_config()
        lazy_ids = [
            agent_id
            for agent_id, ref in config.agents.profiles.items()
            if getattr(ref, "enabled", True)
            and not getattr(ref, "preload", True)
        ]
        if not lazy_ids:
            return

        logger.info(
            "Background pre-warming %d lazy agent(s): %s",
            len(lazy_ids),
            ", ".join(lazy_ids),
        )

        sem = asyncio.Semaphore(max_concurrency)

        async def _warmup_one(agent_id: str) -> None:
            async with sem:
                t = _time.monotonic()
                try:
                    await self.get_agent(agent_id)
                    logger.info(
                        "Agent '%s' pre-warmed in %.1fs",
                        agent_id,
                        _time.monotonic() - t,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to pre-warm agent '%s' (%.1fs): %s",
                        agent_id,
                        _time.monotonic() - t,
                        exc,
                    )

        await asyncio.gather(*[_warmup_one(a) for a in lazy_ids])
        logger.info("Background agent pre-warming complete.")

    def __repr__(self) -> str:
        """String representation of manager."""
        loaded = list(self.agents.keys())
        return f"MultiAgentManager(loaded_agents={loaded})"
