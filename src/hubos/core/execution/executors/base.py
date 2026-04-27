# -*- coding: utf-8 -*-
"""Base executor interface for Parallel Core V1.5 Step 5.

Defines the contract that all executors must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ExecutionResult:
    """Result of a node execution."""

    success: bool
    output: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    executor: str = "base"
    metadata: dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


class BaseExecutor(ABC):
    """Abstract base class for DAG node executors.

    All executors (CAMEL, native, etc.) must implement this interface.
    """

    executor_name: str = "base"

    @abstractmethod
    def execute(
        self,
        node_id: str,
        role: str,
        input_text: str,
        timeout_ms: int,
        attempt: int,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Execute a single DAG node.

        Args:
            node_id: Unique identifier of the node
            role: Role/type of work (e.g., "ceo", "dev", "info")
            input_text: Input text for the node
            timeout_ms: Maximum execution time in milliseconds
            attempt: Current attempt number (1-based)
            metadata: Additional context metadata

        Returns:
            ExecutionResult with success status, output, and timing
        """
        pass

    def supports_role(self, role: str) -> bool:
        """Check if this executor supports a given role.

        Override in subclasses to restrict role support.
        """
        return True

    def get_capabilities(self) -> dict[str, Any]:
        """Return executor capabilities."""
        return {
            "name": self.executor_name,
            "supports_mixed": True,
            "max_parallelism": 50,
        }

    def health_check(self) -> bool:
        """Check if executor is healthy and available."""
        return True
