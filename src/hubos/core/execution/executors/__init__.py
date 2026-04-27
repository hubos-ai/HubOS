# -*- coding: utf-8 -*-
"""Executor abstraction layer for Parallel Core V1.5 Step 5.

This module provides a pluggable executor interface that decouples
the DAG scheduler from specific execution backends like CAMEL or native.
"""

from .base import BaseExecutor, ExecutionResult

__all__ = ["BaseExecutor", "ExecutionResult"]
