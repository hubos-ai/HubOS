"""Pytest configuration and fixtures."""

import logging
import pytest


@pytest.fixture(autouse=True)
def configure_logging() -> None:
    """Configure logging for tests."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


@pytest.fixture
def trace_id() -> str:
    """Provide a test trace ID."""
    return "test-trace-123"


@pytest.fixture
def session_id() -> str:
    """Provide a test session ID."""
    return "test-session-456"


@pytest.fixture
def task_id() -> str:
    """Provide a test task ID."""
    return "test-task-789"
