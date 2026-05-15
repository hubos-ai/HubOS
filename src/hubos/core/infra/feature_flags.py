# -*- coding: utf-8 -*-
"""Feature flags for Week 8 production rollout.

Controls which Week 8 features are enabled/disabled.
All flags default to False to preserve Week 7 behavior when disabled.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class FeatureFlags:
    """Feature flags for production rollout."""

    # Week 7: Policy rollout guard
    enable_policy_rollout_guard: bool = False
    enable_policy_auto_rollback: bool = False

    # Week 7: Memory governance
    enable_memory_budget_enforcement: bool = False

    # Week 7: Hermes persistent retry
    enable_hermes_persistent_retry: bool = False

    # Week 8: PostgreSQL storage
    enable_postgres_store: bool = False

    # Week 8: Distributed locks
    enable_distributed_locks: bool = False

    # Week 8: Release orchestration
    enable_release_orchestration: bool = False

    # Week 11: OpenWork UI Migration
    enable_openwork_ui_migration: bool = False

    # Week 12: Execution Loop MVP
    enable_execution_loop_mvp: bool = False

    # Week 13: WeChat iLink compatibility ingress
    enable_wechat_ilink_compat: bool = False

    # Week 14: WeChat Embedded Plugin V1
    enable_wechat_embedded_plugin: bool = False
    enable_wechat_qr_login_ui: bool = False
    enable_wechat_poller: bool = False

    # Week 13.5: Parallel Core V1.5 Step 1
    enable_camel_backend: bool = False
    enable_parallel_workflow_v1: bool = False
    enable_backend_auto_fallback: bool = False

    # Week 13.5: Parallel Core V1.5 Step 2 - Production Hardening
    enable_camel_real_workforce: bool = False
    enable_parallel_merge_timeout_guard: bool = False
    enable_parallel_branch_persistence: bool = False
    enable_parallel_branch_retry_human_gate: bool = False
    enable_camel_shared_memory: bool = False

    # Week 13.5: Parallel Core V1.5 Step 5 - DAG-native Kernel
    enable_dag_native_engine: bool = False
    enable_dag_executor_mixed_mode: bool = False
    enable_dag_persistence_v1: bool = False
    enable_dag_api_v1: bool = False
    enable_openwork_dag_view: bool = False

    # Week 13.5: Parallel Core V1.5 Step 6 - DAG Intelligence
    enable_dag_policy_learning: bool = False
    enable_dag_conditional_edges: bool = False
    enable_dag_adaptive_parallelism: bool = False
    enable_dag_smart_executor_selection: bool = False
    enable_openwork_dag_interactive_view: bool = False
    enable_dag_executor_auto_switch: bool = False

    # Week 13.5: Step 7 - Cross-Task Collaborative Learning
    enable_cross_task_knowledge_graph: bool = False
    enable_policy_transfer_learning: bool = False
    enable_memory_anti_pollution_guard: bool = False
    enable_autonomous_optimization_loop: bool = False
    enable_openwork_learning_console: bool = False

    # Week 13.5: Step 8 - Org-level Autonomy
    enable_org_objective_engine: bool = False
    enable_global_resource_arbiter: bool = False
    enable_cross_channel_negotiation: bool = False
    enable_org_policy_governance: bool = False
    enable_human_ai_cogovernance: bool = False
    enable_openwork_org_console: bool = False

    # Step 8: Channel Architecture (OpenWork takes WeChat)
    # When enabled: WeChat -> OpenWork -> solo-hub (task) -> OpenWork -> WeChat
    # When disabled: solo-hub handles WeChat directly (legacy behavior)
    enable_openwork_wechat_channel: bool = True  # OpenWork接管微信渠道
    enable_openwork_channel_to_runtime: bool = (
        True  # OpenWork转发消息到solo-hub任务API
    )
    enable_runtime_wechat_direct: bool = False  # solo-hub直接收发微信(建议false)

    # Step 9: Real Model Execution
    # When enabled: one_person_default uses real MiniMax model for stage execution
    # When disabled: uses mock/echo implementation
    enable_real_model_execution: bool = True  # 启用真实模型执行

    # Step 10: Work Experience Layer
    #
    # Work Experience Layer Phases:
    #   Phase 4  (flag: enable_work_experience_layer=True)          — card retrieval + storage
    #   Phase 5  (flag: enable_work_experience_prompt_injection=True) — prompt injection
    #   Phase 6  (console UI, governance)                              — always available
    #
    # Rollout Stages:
    #   Stage 0 - OFF (default): Both flags false. No retrieval, no injection.
    #   Stage 1 - OBSERVE: layer=true, injection=false.
    #              Cards accumulate via ReflectionEngine. No prompt impact yet.
    #              hit_count increments on retrieval. effective_count stays 0.
    #   Stage 2 - TARGETED: layer=true, injection=true.
    #              Prompt injection enabled. Monitor effective_count delta.
    #   Stage 3 - BROAD: Full rollout. All tasks get retrieval + injection.
    #
    # Mock vs Real Mode Counter Behavior:
    #   ENABLE_REAL_MODEL_EXECUTION=true  (real model):
    #     hit_count:     increments on every retrieval (pre_execute → retrieve → increment_hit)
    #     effective_count: increments when generate_for_stage returns success with injected cards
    #   ENABLE_REAL_MODEL_EXECUTION=false (mock mode):
    #     hit_count:     increments on retrieval (same as real)
    #     effective_count: NEVER increments — _execute_stage_mock bypasses generate_for_stage
    #     → Cards accumulate hits but effective_count remains 0 in mock-only deployments
    #
    enable_work_experience_layer: bool = True  # 启用经验沉淀层 (v4 active)
    # When enabled: compressed experience hints are injected into LLM prompt
    # When disabled: prompts are assembled without experience hints
    enable_work_experience_prompt_injection: bool = (
        True  # 启用经验注入prompt (v4 active)
    )

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        """Load feature flags from environment variables."""
        return cls(
            enable_policy_rollout_guard=os.environ.get(
                "ENABLE_POLICY_ROLLOUT_GUARD",
                "false",
            ).lower()
            == "true",
            enable_policy_auto_rollback=os.environ.get(
                "ENABLE_POLICY_AUTO_ROLLBACK",
                "false",
            ).lower()
            == "true",
            enable_memory_budget_enforcement=os.environ.get(
                "ENABLE_MEMORY_BUDGET_ENFORCEMENT",
                "false",
            ).lower()
            == "true",
            enable_hermes_persistent_retry=os.environ.get(
                "ENABLE_HERMES_PERSISTENT_RETRY",
                "false",
            ).lower()
            == "true",
            enable_postgres_store=os.environ.get(
                "ENABLE_POSTGRES_STORE",
                "false",
            ).lower()
            == "true",
            enable_distributed_locks=os.environ.get(
                "ENABLE_DISTRIBUTED_LOCKS",
                "false",
            ).lower()
            == "true",
            enable_release_orchestration=os.environ.get(
                "ENABLE_RELEASE_ORCHESTRATION",
                "false",
            ).lower()
            == "true",
            enable_openwork_ui_migration=os.environ.get(
                "ENABLE_OPENWORK_UI_MIGRATION",
                "false",
            ).lower()
            == "true",
            enable_execution_loop_mvp=os.environ.get(
                "ENABLE_EXECUTION_LOOP_MVP",
                "false",
            ).lower()
            == "true",
            enable_wechat_ilink_compat=os.environ.get(
                "ENABLE_WECHAT_ILINK_COMPAT",
                "false",
            ).lower()
            == "true",
            enable_wechat_embedded_plugin=os.environ.get(
                "ENABLE_WECHAT_EMBEDDED_PLUGIN",
                "false",
            ).lower()
            == "true",
            enable_wechat_qr_login_ui=os.environ.get(
                "ENABLE_WECHAT_QR_LOGIN_UI",
                "false",
            ).lower()
            == "true",
            enable_wechat_poller=os.environ.get(
                "ENABLE_WECHAT_POLLER",
                "false",
            ).lower()
            == "true",
            enable_camel_backend=os.environ.get(
                "ENABLE_CAMEL_BACKEND",
                "false",
            ).lower()
            == "true",
            enable_parallel_workflow_v1=os.environ.get(
                "ENABLE_PARALLEL_WORKFLOW_V1",
                "false",
            ).lower()
            == "true",
            enable_backend_auto_fallback=os.environ.get(
                "ENABLE_BACKEND_AUTO_FALLBACK",
                "false",
            ).lower()
            == "true",
            enable_camel_real_workforce=os.environ.get(
                "ENABLE_CAMEL_REAL_WORKFORCE",
                "false",
            ).lower()
            == "true",
            enable_parallel_merge_timeout_guard=os.environ.get(
                "ENABLE_PARALLEL_MERGE_TIMEOUT_GUARD",
                "false",
            ).lower()
            == "true",
            enable_parallel_branch_persistence=os.environ.get(
                "ENABLE_PARALLEL_BRANCH_PERSISTENCE",
                "false",
            ).lower()
            == "true",
            enable_parallel_branch_retry_human_gate=os.environ.get(
                "ENABLE_PARALLEL_BRANCH_RETRY_HUMAN_GATE",
                "false",
            ).lower()
            == "true",
            enable_camel_shared_memory=os.environ.get(
                "ENABLE_CAMEL_SHARED_MEMORY",
                "false",
            ).lower()
            == "true",
            enable_dag_native_engine=os.environ.get(
                "ENABLE_DAG_NATIVE_ENGINE",
                "false",
            ).lower()
            == "true",
            enable_dag_executor_mixed_mode=os.environ.get(
                "ENABLE_DAG_EXECUTOR_MIXED_MODE",
                "false",
            ).lower()
            == "true",
            enable_dag_persistence_v1=os.environ.get(
                "ENABLE_DAG_PERSISTENCE_V1",
                "false",
            ).lower()
            == "true",
            enable_dag_api_v1=os.environ.get(
                "ENABLE_DAG_API_V1",
                "false",
            ).lower()
            == "true",
            enable_openwork_dag_view=os.environ.get(
                "ENABLE_OPENWORK_DAG_VIEW",
                "false",
            ).lower()
            == "true",
            enable_dag_policy_learning=os.environ.get(
                "ENABLE_DAG_POLICY_LEARNING",
                "false",
            ).lower()
            == "true",
            enable_dag_conditional_edges=os.environ.get(
                "ENABLE_DAG_CONDITIONAL_EDGES",
                "false",
            ).lower()
            == "true",
            enable_dag_adaptive_parallelism=os.environ.get(
                "ENABLE_DAG_ADAPTIVE_PARALLELISM",
                "false",
            ).lower()
            == "true",
            enable_dag_smart_executor_selection=os.environ.get(
                "ENABLE_DAG_SMART_EXECUTOR_SELECTION",
                "false",
            ).lower()
            == "true",
            enable_openwork_dag_interactive_view=os.environ.get(
                "ENABLE_OPENWORK_DAG_INTERACTIVE_VIEW",
                "false",
            ).lower()
            == "true",
            enable_dag_executor_auto_switch=os.environ.get(
                "ENABLE_DAG_EXECUTOR_AUTO_SWITCH",
                "false",
            ).lower()
            == "true",
            enable_cross_task_knowledge_graph=os.environ.get(
                "ENABLE_CROSS_TASK_KNOWLEDGE_GRAPH",
                "false",
            ).lower()
            == "true",
            enable_policy_transfer_learning=os.environ.get(
                "ENABLE_POLICY_TRANSFER_LEARNING",
                "false",
            ).lower()
            == "true",
            enable_memory_anti_pollution_guard=os.environ.get(
                "ENABLE_MEMORY_ANTI_POLLUTION_GUARD",
                "false",
            ).lower()
            == "true",
            enable_autonomous_optimization_loop=os.environ.get(
                "ENABLE_AUTONOMOUS_OPTIMIZATION_LOOP",
                "false",
            ).lower()
            == "true",
            enable_openwork_learning_console=os.environ.get(
                "ENABLE_OPENWORK_LEARNING_CONSOLE",
                "false",
            ).lower()
            == "true",
            enable_org_objective_engine=os.environ.get(
                "ENABLE_ORG_OBJECTIVE_ENGINE",
                "false",
            ).lower()
            == "true",
            enable_global_resource_arbiter=os.environ.get(
                "ENABLE_GLOBAL_RESOURCE_ARBITER",
                "false",
            ).lower()
            == "true",
            enable_cross_channel_negotiation=os.environ.get(
                "ENABLE_CROSS_CHANNEL_NEGOTIATION",
                "false",
            ).lower()
            == "true",
            enable_org_policy_governance=os.environ.get(
                "ENABLE_ORG_POLICY_GOVERNANCE",
                "false",
            ).lower()
            == "true",
            enable_human_ai_cogovernance=os.environ.get(
                "ENABLE_HUMAN_AI_COGOVERNANCE",
                "false",
            ).lower()
            == "true",
            enable_openwork_org_console=os.environ.get(
                "ENABLE_OPENWORK_ORG_CONSOLE",
                "false",
            ).lower()
            == "true",
            enable_openwork_wechat_channel=os.environ.get(
                "ENABLE_OPENWORK_WECHAT_CHANNEL",
                "true",
            ).lower()
            == "true",
            enable_openwork_channel_to_runtime=os.environ.get(
                "ENABLE_OPENWORK_CHANNEL_TO_RUNTIME",
                "true",
            ).lower()
            == "true",
            enable_runtime_wechat_direct=os.environ.get(
                "RUNTIME_WECHAT_DIRECT",
                "false",
            ).lower()
            == "true",
            enable_real_model_execution=os.environ.get(
                "ENABLE_REAL_MODEL_EXECUTION",
                "true",
            ).lower()
            == "true",
            enable_work_experience_layer=os.environ.get(
                "ENABLE_WORK_EXPERIENCE_LAYER",
                "true",
            ).lower()
            == "true",
            enable_work_experience_prompt_injection=os.environ.get(
                "ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION",
                "true",
            ).lower()
            == "true",
        )

    def is_enabled(self, flag_name: str) -> bool:
        """Check if a specific flag is enabled."""
        return getattr(self, flag_name, False)

    def get_enabled_features(self) -> dict[str, bool]:
        """Get dict of all enabled/disabled features."""
        return {
            "enable_policy_rollout_guard": self.enable_policy_rollout_guard,
            "enable_policy_auto_rollback": self.enable_policy_auto_rollback,
            "enable_memory_budget_enforcement": self.enable_memory_budget_enforcement,
            "enable_hermes_persistent_retry": self.enable_hermes_persistent_retry,
            "enable_postgres_store": self.enable_postgres_store,
            "enable_distributed_locks": self.enable_distributed_locks,
            "enable_release_orchestration": self.enable_release_orchestration,
            "enable_openwork_ui_migration": self.enable_openwork_ui_migration,
            "enable_execution_loop_mvp": self.enable_execution_loop_mvp,
            "enable_wechat_ilink_compat": self.enable_wechat_ilink_compat,
            "enable_wechat_embedded_plugin": self.enable_wechat_embedded_plugin,
            "enable_wechat_qr_login_ui": self.enable_wechat_qr_login_ui,
            "enable_wechat_poller": self.enable_wechat_poller,
            "enable_camel_backend": self.enable_camel_backend,
            "enable_parallel_workflow_v1": self.enable_parallel_workflow_v1,
            "enable_backend_auto_fallback": self.enable_backend_auto_fallback,
            "enable_camel_real_workforce": self.enable_camel_real_workforce,
            "enable_parallel_merge_timeout_guard": self.enable_parallel_merge_timeout_guard,
            "enable_parallel_branch_persistence": self.enable_parallel_branch_persistence,
            "enable_parallel_branch_retry_human_gate": self.enable_parallel_branch_retry_human_gate,
            "enable_camel_shared_memory": self.enable_camel_shared_memory,
            "enable_dag_native_engine": self.enable_dag_native_engine,
            "enable_dag_executor_mixed_mode": self.enable_dag_executor_mixed_mode,
            "enable_dag_persistence_v1": self.enable_dag_persistence_v1,
            "enable_dag_api_v1": self.enable_dag_api_v1,
            "enable_openwork_dag_view": self.enable_openwork_dag_view,
            "enable_dag_policy_learning": self.enable_dag_policy_learning,
            "enable_dag_conditional_edges": self.enable_dag_conditional_edges,
            "enable_dag_adaptive_parallelism": self.enable_dag_adaptive_parallelism,
            "enable_dag_smart_executor_selection": self.enable_dag_smart_executor_selection,
            "enable_openwork_dag_interactive_view": self.enable_openwork_dag_interactive_view,
            "enable_dag_executor_auto_switch": self.enable_dag_executor_auto_switch,
            "enable_cross_task_knowledge_graph": self.enable_cross_task_knowledge_graph,
            "enable_policy_transfer_learning": self.enable_policy_transfer_learning,
            "enable_memory_anti_pollution_guard": self.enable_memory_anti_pollution_guard,
            "enable_autonomous_optimization_loop": self.enable_autonomous_optimization_loop,
            "enable_openwork_learning_console": self.enable_openwork_learning_console,
            "enable_org_objective_engine": self.enable_org_objective_engine,
            "enable_global_resource_arbiter": self.enable_global_resource_arbiter,
            "enable_cross_channel_negotiation": self.enable_cross_channel_negotiation,
            "enable_org_policy_governance": self.enable_org_policy_governance,
            "enable_human_ai_cogovernance": self.enable_human_ai_cogovernance,
            "enable_openwork_org_console": self.enable_openwork_org_console,
            "enable_openwork_wechat_channel": self.enable_openwork_wechat_channel,
            "enable_openwork_channel_to_runtime": self.enable_openwork_channel_to_runtime,
            "enable_runtime_wechat_direct": self.enable_runtime_wechat_direct,
            "enable_work_experience_layer": self.enable_work_experience_layer,
            "enable_work_experience_prompt_injection": self.enable_work_experience_prompt_injection,
        }

    # ==================== Week 8 Step 8 Org Autonomy Convenience Methods ====================

    def use_org_objective_engine(self) -> bool:
        """Check if org objective engine is enabled."""
        return self.enable_org_objective_engine

    def use_global_resource_arbiter(self) -> bool:
        """Check if global resource arbiter is enabled."""
        return self.enable_global_resource_arbiter

    def use_cross_channel_negotiation(self) -> bool:
        """Check if cross-channel negotiation is enabled."""
        return self.enable_cross_channel_negotiation

    def use_org_policy_governance(self) -> bool:
        """Check if org policy governance is enabled."""
        return self.enable_org_policy_governance

    def use_human_ai_cogovernance(self) -> bool:
        """Check if human-AI co-governance is enabled."""
        return self.enable_human_ai_cogovernance

    def use_openwork_org_console(self) -> bool:
        """Check if OpenWork org console is enabled."""
        return (
            self.enable_openwork_org_console
            and self.enable_org_objective_engine
        )

    def use_openwork_wechat_channel(self) -> bool:
        """Check if OpenWork WeChat channel is enabled.

        When true: WeChat -> OpenWork -> solo-hub -> OpenWork -> WeChat
        When false: solo-hub handles WeChat directly (legacy)
        """
        return self.enable_openwork_wechat_channel

    def use_openwork_channel_to_runtime(self) -> bool:
        """Check if OpenWork routes WeChat messages to solo-hub task API."""
        return self.enable_openwork_channel_to_runtime

    def use_runtime_wechat_direct(self) -> bool:
        """Check if solo-hub can directly send/receive WeChat (legacy mode).

        Default is False - solo-hub should go through OpenWork.
        Set to True only for legacy deployments or emergency rollback.
        """
        return self.enable_runtime_wechat_direct

    def is_org_autonomy_enabled(self) -> bool:
        """Check if any org autonomy feature is enabled."""
        return any(
            [
                self.enable_org_objective_engine,
                self.enable_global_resource_arbiter,
                self.enable_cross_channel_negotiation,
                self.enable_org_policy_governance,
                self.enable_human_ai_cogovernance,
            ],
        )

    # ==================== Week 8 Convenience Methods ====================

    def use_postgres(self) -> bool:
        """Check if PostgreSQL store should be used."""
        return self.enable_postgres_store

    def use_distributed_locks(self) -> bool:
        """Check if distributed locks should be used (requires Redis)."""
        return self.enable_distributed_locks and bool(
            os.environ.get("REDIS_HOST"),
        )

    def is_multi_instance_mode(self) -> bool:
        """Check if running in multi-instance mode."""
        return self.use_distributed_locks() or self.use_postgres()

    # ==================== Week 13.5 Step 5 DAG-native Convenience Methods ====================

    def use_dag_native_engine(self) -> bool:
        """Check if DAG-native engine should be used."""
        return self.enable_dag_native_engine

    def use_dag_executor_mixed_mode(self) -> bool:
        """Check if mixed executor mode is enabled."""
        return (
            self.enable_dag_executor_mixed_mode
            and self.enable_dag_native_engine
        )

    def use_dag_persistence(self) -> bool:
        """Check if DAG persistence is enabled."""
        return self.enable_dag_persistence_v1 and self.enable_dag_native_engine

    def use_dag_api(self) -> bool:
        """Check if DAG API v1 is enabled."""
        return self.enable_dag_api_v1 and self.enable_dag_native_engine

    def use_openwork_dag_view(self) -> bool:
        """Check if OpenWork DAG view is enabled."""
        return self.enable_openwork_dag_view and self.enable_dag_native_engine

    def is_dag_enabled(self) -> bool:
        """Check if any DAG feature is enabled."""
        return self.enable_dag_native_engine

    # ==================== Week 13.5 Step 6 DAG Intelligence Convenience Methods ====================

    def use_dag_policy_learning(self) -> bool:
        """Check if DAG policy learning is enabled."""
        return (
            self.enable_dag_policy_learning and self.enable_dag_native_engine
        )

    def use_dag_conditional_edges(self) -> bool:
        """Check if DAG conditional edge routing is enabled."""
        return (
            self.enable_dag_conditional_edges and self.enable_dag_native_engine
        )

    def use_dag_adaptive_parallelism(self) -> bool:
        """Check if DAG adaptive parallelism is enabled."""
        return (
            self.enable_dag_adaptive_parallelism
            and self.enable_dag_native_engine
        )

    def use_dag_smart_executor_selection(self) -> bool:
        """Check if DAG smart executor selection is enabled."""
        return (
            self.enable_dag_smart_executor_selection
            and self.enable_dag_native_engine
        )

    def use_openwork_dag_interactive_view(self) -> bool:
        """Check if OpenWork DAG interactive view is enabled."""
        return (
            self.enable_openwork_dag_interactive_view
            and self.enable_dag_native_engine
        )

    def use_dag_executor_auto_switch(self) -> bool:
        """Check if DAG executor auto-switch on failure is enabled."""
        return (
            self.enable_dag_executor_auto_switch
            and self.enable_dag_native_engine
        )

    def is_dag_intelligence_enabled(self) -> bool:
        """Check if any DAG intelligence feature is enabled."""
        return (
            self.enable_dag_policy_learning
            or self.enable_dag_conditional_edges
            or self.enable_dag_adaptive_parallelism
            or self.enable_dag_smart_executor_selection
        ) and self.enable_dag_native_engine

    # ==================== Step 7 Cross-Task Learning Convenience Methods ====================

    def use_cross_task_knowledge_graph(self) -> bool:
        """Check if cross-task knowledge graph is enabled."""
        return self.enable_cross_task_knowledge_graph

    def use_policy_transfer_learning(self) -> bool:
        """Check if policy transfer learning is enabled."""
        return (
            self.enable_policy_transfer_learning
            and self.enable_cross_task_knowledge_graph
        )

    def use_memory_anti_pollution_guard(self) -> bool:
        """Check if memory anti-pollution guard is enabled."""
        return self.enable_memory_anti_pollution_guard

    def use_autonomous_optimization_loop(self) -> bool:
        """Check if autonomous optimization loop is enabled."""
        return (
            self.enable_autonomous_optimization_loop
            and self.enable_cross_task_knowledge_graph
        )

    def use_openwork_learning_console(self) -> bool:
        """Check if OpenWork learning console is enabled."""
        return (
            self.enable_openwork_learning_console
            and self.enable_cross_task_knowledge_graph
        )

    def is_cross_task_learning_enabled(self) -> bool:
        """Check if any cross-task learning feature is enabled."""
        return (
            self.enable_cross_task_knowledge_graph
            or self.enable_policy_transfer_learning
            or self.enable_memory_anti_pollution_guard
            or self.enable_autonomous_optimization_loop
        )

    def use_work_experience(self) -> bool:
        """Check if full work experience layer (retrieval + injection) is enabled.

        Both enable_work_experience_layer AND enable_work_experience_prompt_injection
        must be True for full operation. If only the layer is enabled, cards are
        retrieved and stored but not injected into prompts.
        """
        return (
            self.enable_work_experience_layer
            and self.enable_work_experience_prompt_injection
        )

    def work_experience_stage(self) -> str:
        """Return the current work experience rollout stage.

        Returns:
            "OFF"     — neither flag enabled
            "OBSERVE" — layer=true, injection=false (cards accumulate, no prompt impact)
            "TARGETED" — layer=true, injection=true (full retrieval+injection)
        """
        if not self.enable_work_experience_layer:
            return "OFF"
        if self.enable_work_experience_prompt_injection:
            return "TARGETED"
        return "OBSERVE"


# Global feature flags instance
_feature_flags: Optional[FeatureFlags] = None


def get_feature_flags() -> FeatureFlags:
    """Get global feature flags instance."""
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = FeatureFlags.from_env()
    return _feature_flags


def reload_feature_flags() -> FeatureFlags:
    """Reload feature flags from environment."""
    global _feature_flags
    _feature_flags = FeatureFlags.from_env()
    return _feature_flags
