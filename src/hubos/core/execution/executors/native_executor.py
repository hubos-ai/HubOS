"""Native executor for Parallel Core V1.5 Step 5.

Fallback executor that processes DAG nodes directly without CAMEL.
"""

import time
from typing import Any, Optional

from .base import BaseExecutor, ExecutionResult


class NativeExecutor(BaseExecutor):
    """Executor that processes nodes directly.

    This is the fallback executor when CAMEL is unavailable.
    It processes nodes synchronously with minimal overhead.
    """

    executor_name = "native"

    def __init__(self, agent_registry=None) -> None:
        """Initialize native executor.

        Args:
            agent_registry: Optional agent registry for routing
        """
        self._agent_registry = agent_registry

    def execute(
        self,
        node_id: str,
        role: str,
        input_text: str,
        timeout_ms: int,
        attempt: int,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Execute a node natively (synchronous processing)."""
        start_time = time.time()

        try:
            # Check if real model execution is enabled
            from hubos.core.infra.feature_flags import get_feature_flags
            flags = get_feature_flags()

            if flags.enable_real_model_execution:
                return self._execute_with_llm(node_id, role, input_text, timeout_ms, attempt, metadata, start_time)
            else:
                return self._execute_mock(node_id, role, input_text, timeout_ms, attempt, metadata, start_time)

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=False,
                error=f"Native execution failed: {str(e)}",
                duration_ms=duration_ms,
                executor=self.executor_name,
            )

    def _execute_with_llm(
        self,
        node_id: str,
        role: str,
        input_text: str,
        timeout_ms: int,
        attempt: int,
        metadata: Optional[dict[str, Any]],
        start_time: float,
    ) -> ExecutionResult:
        """Execute using real LLM model."""
        from hubos.core.llm.runtime import get_llm_runtime

        llm = get_llm_runtime()
        formatted_input = self._format_input(role, input_text, metadata)

        try:
            result = llm.generate_for_stage(
                stage=role.lower(),
                input_text=formatted_input,
                context={"node_id": node_id},
            )

            duration_ms = (time.time() - start_time) * 1000

            if result.success:
                return ExecutionResult(
                    success=True,
                    output={
                        "response_text": result.text,
                        "role": role,
                        "node_id": node_id,
                        "confidence": 0.9,
                    },
                    duration_ms=duration_ms,
                    executor=self.executor_name,
                    metadata={
                        "attempt": attempt,
                        "timeout_ms": timeout_ms,
                        "llm_used": True,
                    },
                )
            else:
                return ExecutionResult(
                    success=False,
                    error=f"LLM generation failed: {result.error}",
                    duration_ms=duration_ms,
                    executor=self.executor_name,
                    metadata={"attempt": attempt, "timeout_ms": timeout_ms},
                )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=False,
                error=f"LLM execution error: {str(e)}",
                duration_ms=duration_ms,
                executor=self.executor_name,
                metadata={"attempt": attempt, "timeout_ms": timeout_ms},
            )

    def _execute_mock(
        self,
        node_id: str,
        role: str,
        input_text: str,
        timeout_ms: int,
        attempt: int,
        metadata: Optional[dict[str, Any]],
        start_time: float,
    ) -> ExecutionResult:
        """Execute with mock (DEPRECATED, only when real model disabled)."""
        formatted_input = self._format_input(role, input_text, metadata)
        result_text = f"[{role.upper()}] Processed: {formatted_input}"

        duration_ms = (time.time() - start_time) * 1000

        return ExecutionResult(
            success=True,
            output={
                "response_text": result_text,
                "role": role,
                "node_id": node_id,
                "confidence": 0.8,
            },
            duration_ms=duration_ms,
            executor=self.executor_name,
            metadata={
                "attempt": attempt,
                "timeout_ms": timeout_ms,
                "mock": True,
            },
        )

    def _format_input(
        self,
        role: str,
        input_text: str,
        metadata: Optional[dict[str, Any]],
    ) -> str:
        """Format input text based on role."""
        if metadata and metadata.get("input_template"):
            template = metadata["input_template"]
            return template.format(input=input_text)
        return input_text

    def supports_role(self, role: str) -> bool:
        """Native executor supports all roles."""
        return True

    def get_capabilities(self) -> dict[str, Any]:
        """Return native executor capabilities."""
        return {
            "name": self.executor_name,
            "supports_mixed": True,
            "max_parallelism": 10,
            "roles": ["ceo", "info", "dev", "review", "planner", "analyst", "any"],
            "fallback": True,
        }

    def health_check(self) -> bool:
        """Native executor is always healthy (local processing)."""
        return True
