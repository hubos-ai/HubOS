# -*- coding: utf-8 -*-
"""LLM Service for real model execution.

Provides unified interface for model generation across different providers.
"""

from .runtime import LLMRuntime, get_llm_runtime

__all__ = ["LLMRuntime", "get_llm_runtime"]
