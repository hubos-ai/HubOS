# -*- coding: utf-8 -*-
"""LLM Runtime - unified interface for model execution.

Provides a single entry point for executing prompts through configured LLM providers.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from .providers.minimax_provider import MiniMaxProvider, MiniMaxResponse

logger = logging.getLogger(__name__)


# Stage role prompts for one_person_default workflow
STAGE_PROMPTS = {
    "ceo": {
        "system": "You are a strategic thinking assistant. Analyze the user's request and provide a high-level plan or summary. Be concise and actionable. Your output is for internal use only - do not make it user-facing.",
        "user_template": "Analyze this request: {input}",
        "internal_only": True,
    },
    "info": {
        "system": "You are an information gathering and analysis assistant. Collect and organize relevant information for the request. Be thorough but concise. Your output is for internal use only.",
        "user_template": "Gather and organize information for: {input}",
        "internal_only": True,
    },
    "dev": {
        "system": "You are a development and implementation assistant. Provide concrete solutions, code, or actionable steps to address the request. Be practical and specific. Your output is for internal use only.",
        "user_template": "Implement a solution for: {input}",
        "internal_only": True,
    },
    "review": {
        "system": """You are a friendly AI assistant giving the final answer to a user.
CRITICAL RULES - VIOLATE THESE AND THE USER WILL COMPLAIN:
1. Output ONLY the direct answer text - NOTHING ELSE
2. DO NOT include your thinking process, DO NOT include <think> or </thinking> tags
3. DO NOT include section headers like ## or ###
4. DO NOT include "用户意图", "关键考量", "战略分析", "执行建议"
5. DO NOT use markdown formatting (no **bold**, no ## headers, no bullet points unless part of answer)
6. Write as if talking to a friend - conversational, short sentences
7. If user asks "你是谁" or "你能做什么" - reply briefly in 1-3 sentences naturally

GOOD example for "你是谁":
"你好！我是AI助手，帮你回答问题、写代码、分析数据。有什么要帮忙的吗？"

BAD example (don't do this):
<think>
用户问我是谁...
</think>
## 战略分析
用户意图是...
""",
        "user_template": """直接回答：{input}

禁止输出：
- <think>任何思考过程...
- 任何##标题
- 任何内部分析
只输出给用户的自然语言答案！""",
        "internal_only": False,
    },
}


@dataclass
class GenerationResult:
    """Result from LLM generation."""

    text: str
    success: bool
    error: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[dict[str, int]] = None
    model: Optional[str] = None


class LLMRuntime:
    """Unified LLM runtime for executing prompts.

    Supports multiple providers with MiniMax as the primary provider.
    """

    def __init__(
        self,
        provider: Optional[MiniMaxProvider] = None,
        enable_fallback: bool = True,
    ) -> None:
        """Initialize LLM runtime.

        Args:
            provider: LLM provider instance (creates MiniMaxProvider if not provided)
            enable_fallback: Enable fallback to mock if provider fails
        """
        self._provider = provider or MiniMaxProvider()
        self._enable_fallback = enable_fallback

    @property
    def provider(self) -> MiniMaxProvider:
        """Get the underlying provider."""
        return self._provider

    def is_available(self) -> bool:
        """Check if the LLM runtime is available (provider configured)."""
        return self._provider.is_configured

    def generate_for_stage(
        self,
        stage: str,
        input_text: str,
        context: Optional[dict[str, Any]] = None,
    ) -> GenerationResult:
        """Generate text for a workflow stage.

        Args:
            stage: Stage name (ceo, info, dev, review)
            input_text: User input text
            context: Optional context dictionary

        Returns:
            GenerationResult with generated text
        """
        if not self._provider.is_configured:
            if self._enable_fallback:
                logger.warning(
                    f"LLM provider not configured, using fallback for stage {stage}",
                )
                return self._fallback_response(stage, input_text)
            return GenerationResult(
                text="",
                success=False,
                error="LLM provider not configured",
            )

        stage_config = STAGE_PROMPTS.get(stage.lower(), {})
        system_prompt = stage_config.get(
            "system",
            "You are a helpful assistant.",
        )
        user_template = stage_config.get("user_template", "{input}")

        user_prompt = user_template.format(input=input_text)

        # Phase 5-A: Inject compressed experience hints into user prompt
        user_prompt = self._inject_work_experience(user_prompt, context)

        try:
            response = self._provider.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=1024,
                role=stage,
            )

            # For review stage, clean up any remaining prefixes
            text = response.text
            if stage.lower() == "review":
                text = self._clean_review_output(text)

            return GenerationResult(
                text=text,
                success=True,
                finish_reason=response.finish_reason,
                usage={
                    "prompt_tokens": response.usage.get("prompt_tokens")
                    if response.usage
                    else None,
                    "completion_tokens": response.usage.get(
                        "completion_tokens",
                    )
                    if response.usage
                    else None,
                    "total_tokens": response.usage.get("total_tokens")
                    if response.usage
                    else None,
                }
                if response.usage
                else None,
                model=response.model,
            )

        except Exception as e:
            logger.error(f"LLM generation failed for stage {stage}: {e}")
            if self._enable_fallback:
                return self._fallback_response(stage, input_text)
            return GenerationResult(
                text="",
                success=False,
                error=str(e),
            )

    def _inject_work_experience(
        self,
        user_prompt: str,
        context: Optional[dict[str, Any]],
    ) -> str:
        """
        Phase 5-A: Inject compressed work experience hints into the user prompt.

        Controlled by ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION flag.
        Extracts work_experience_cards from context and injects them as a compact
        hint block prepended to the user prompt.

        Hard constraints enforced:
        - Does NOT modify system prompt, tools, or skills
        - Does NOT auto-select tools or change routing
        - Empty cards → no injection (passthrough)
        - Per-card limit: DEFAULT_MAX_CHARS_PER_CARD (150 chars)
        - Total injection budget: DEFAULT_MAX_TOTAL_CHARS (350 chars)
        - top_k: DEFAULT_MAX_K (1 card)

        Injection parameters are sourced from prompt_injector.DEFAULT_MAX_K,
        DEFAULT_MAX_CHARS_PER_CARD, and DEFAULT_MAX_TOTAL_CHARS — a single
        source of truth shared by both the injector and this runtime caller.

        Args:
            user_prompt: The assembled user prompt
            context: Optional context dict that may contain work_experience_cards

        Returns:
            Original prompt, or prompt with experience hint prepended
        """
        from hubos.core.infra.feature_flags import get_feature_flags

        if not get_feature_flags().enable_work_experience_prompt_injection:
            return user_prompt

        cards = None
        if context:
            cards = context.get("work_experience_cards")

        if not cards:
            return user_prompt

        from hubos.core.work_experience.prompt_injector import (
            DEFAULT_MAX_K,
            DEFAULT_MAX_CHARS_PER_CARD,
            DEFAULT_MAX_TOTAL_CHARS,
            inject_experience_into_prompt,
        )

        return inject_experience_into_prompt(
            user_prompt=user_prompt,
            cards=cards,
            max_k=DEFAULT_MAX_K,
            max_chars_per_card=DEFAULT_MAX_CHARS_PER_CARD,
            max_total_chars=DEFAULT_MAX_TOTAL_CHARS,
        )

    def generate_direct(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> GenerationResult:
        """Generate text directly (no stage template).

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            GenerationResult with generated text
        """
        if not self._provider.is_configured:
            if self._enable_fallback:
                return GenerationResult(
                    text=f"[Fallback] {prompt}",
                    success=True,
                    error="Using fallback (provider not configured)",
                )
            return GenerationResult(
                text="",
                success=False,
                error="LLM provider not configured",
            )

        try:
            response = self._provider.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            return GenerationResult(
                text=response.text,
                success=True,
                finish_reason=response.finish_reason,
                usage={
                    "prompt_tokens": response.usage.get("prompt_tokens")
                    if response.usage
                    else None,
                    "completion_tokens": response.usage.get(
                        "completion_tokens",
                    )
                    if response.usage
                    else None,
                    "total_tokens": response.usage.get("total_tokens")
                    if response.usage
                    else None,
                }
                if response.usage
                else None,
                model=response.model,
            )

        except Exception as e:
            logger.error(f"LLM direct generation failed: {e}")
            if self._enable_fallback:
                return GenerationResult(
                    text=f"[Fallback] {prompt}",
                    success=True,
                    error=str(e),
                )
            return GenerationResult(
                text="",
                success=False,
                error=str(e),
            )

    def _clean_review_output(self, text: str) -> str:
        """Clean review stage output to remove any internal content headers/labels.

        This is a fallback sanitizer - the primary defense is the prompt engineering.
        """
        import re

        # Step 0: Remove AI thinking tags like <think>...</think>
        text = re.sub(r"<think>[\s\S]*?</think>", "", text)

        # Step 1: Basic cleanup
        text = text.strip()

        # Step 2: Remove leading stage labels like [REVIEW], [CEO], [INFO], etc.
        # Remove repeated labels like [REVIEW]\n[REVIEW] and labels followed by content
        text = re.sub(
            r"^\[[A-Z]+\]\s*",
            "",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        text = re.sub(
            r"^\[[A-Z]+\]\s*\n\s*",
            "",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        text = re.sub(r"\n\[[A-Z]+\]\s*", "\n", text, flags=re.IGNORECASE)

        # Step 3: Remove section headers that indicate internal analysis
        internal_headers = [
            r"^##\s*战略分析",
            r"^##\s*用户意图",
            r"^##\s*关键考量",
            r"^##\s*执行建议",
            r"^##\s*思路",
            r"^##\s*分析",
            r"^##\s*总结",
            r"^###\s*",
            r"^#\s*",
        ]
        for pattern in internal_headers:
            text = re.sub(pattern, "", text, flags=re.MULTILINE)

        # Step 4: Remove lines that start with internal content indicators
        lines = text.split("\n")
        cleaned_lines = []
        for line in lines:
            # Skip lines that are clearly internal notes
            if re.match(r"^用户意图[：:]\s*", line):
                continue
            if re.match(r"^关键考量[：:]\s*", line):
                continue
            if re.match(r"^战略分析[：:]\s*", line):
                continue
            if re.match(r"^执行建议[：:]\s*", line):
                continue
            if re.match(r"^思路[：:]\s*", line):
                continue
            if re.match(r"^\*\*.*\*\*$", line) and len(line) < 20:
                # Skip short bold lines that are likely labels
                continue
            if (
                re.match(r"^\*\*", line)
                and "**\n" not in line
                and "**." not in line
            ):
                # Skip bold lines that are labels without proper sentence ending
                continue
            cleaned_lines.append(line)

        text = "\n".join(cleaned_lines)

        # Step 5: Remove multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Step 6: Final trim
        text = text.strip()

        # Step 7: If text still looks like internal analysis (has ## or ### anywhere), do aggressive cleanup
        if "##" in text or "###" in text:
            # Remove everything from ## onwards
            text = re.sub(r"\n##.*", "", text, flags=re.DOTALL)
            text = text.strip()

        # Step 8: Remove any remaining markdown bold/headers at the start
        text = re.sub(r"^#+\s*", "", text)
        text = re.sub(r"^\*\*+\s*", "", text)
        text = re.sub(r"\*\*+$", "", text)

        return text.strip()

    def _fallback_response(
        self,
        stage: str,
        input_text: str,
    ) -> GenerationResult:
        """Generate fallback response when LLM is unavailable.

        Returns a helpful but honest response indicating the system state.
        """
        # Don't use "Processed:" - use a meaningful fallback
        fallbacks = {
            "ceo": f"I've analyzed your request: {input_text[:50]}...\n\nBased on my analysis, I understand what you're asking about.",
            "info": f"Let me gather information about: {input_text[:50]}...\n\nBased on my knowledge, I can provide some context on this topic.",
            "dev": f"For your request: {input_text[:50]}...\n\nHere's a practical approach to consider.",
            "review": f"Regarding your question about: {input_text[:50]}...\n\nBased on my analysis, here's what I can tell you.",
        }

        return GenerationResult(
            text=fallbacks.get(
                stage.lower(),
                f"Processing: {input_text[:50]}...",
            ),
            success=True,
            error="Fallback response (LLM unavailable)",
        )


# Global runtime instance (lazy initialization)
_runtime: Optional[LLMRuntime] = None


def get_llm_runtime() -> LLMRuntime:
    """Get or create the global LLM runtime instance.

    Returns:
        LLMRuntime singleton
    """
    global _runtime
    if _runtime is None:
        _runtime = LLMRuntime()
    return _runtime


def reset_llm_runtime() -> None:
    """Reset the global LLM runtime (for testing)."""
    global _runtime
    _runtime = None
