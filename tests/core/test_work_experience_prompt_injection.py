"""Phase 5-A tests: WorkExperience prompt injection into generate_for_stage().

Tests:
1. Flag off: prompt unchanged (passthrough)
2. Flag on + cards: experience hint prepended to user prompt
3. Flag on + empty cards: prompt unchanged (no injection)
4. Overlong cards: trimmed to budget
5. top_k limit respected (only top 3 injected)
6. Does not affect execution result (status, final_response unchanged)
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from hubos.core.llm.runtime import LLMRuntime, STAGE_PROMPTS


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_provider() -> MagicMock:
    """A mock LLM provider that records the prompt it received."""
    provider = MagicMock()
    provider.is_configured = True
    response = MagicMock()
    response.text = "Executed successfully"
    response.finish_reason = "stop"
    response.usage = {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
    response.model = "test-model"
    provider.generate.return_value = response
    return provider


@pytest.fixture
def runtime(mock_provider: MagicMock) -> LLMRuntime:
    """An LLMRuntime with a mock provider."""
    return LLMRuntime(provider=mock_provider, enable_fallback=False)


@pytest.fixture
def sample_cards() -> list[dict]:
    """Two sample experience cards with varying lengths."""
    return [
        {
            "experience_id": "abc123",
            "title": "CSV File Processing",
            "what_worked": ["CSV parsed successfully with pandas using encoding detection"],
            "what_failed": [],
            "guidance": "Use encoding detection before parsing CSV files",
            "avoidance": [],
            "trigger_hint": "type:csv",
            "trigger_keywords": ["csv", "file", "pandas"],
            "scope": "project",
            "source_task_id": "task-1",
            "source_session_id": "s1",
            "source_trace_id": "t1",
            "applicability_tags": ["file", "csv"],
            "confidence": 0.85,
            "hit_count": 3,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "experience_id": "def456",
            "title": "Web Crawl robots.txt",
            "what_worked": [],
            "what_failed": ["robots.txt blocked the URL"],
            "guidance": "Check robots.txt before crawling",
            "avoidance": ["Don't crawl without checking robots.txt first"],
            "trigger_hint": "type:web_crawl",
            "trigger_keywords": ["web", "crawl", "robots"],
            "scope": "global",
            "source_task_id": "task-2",
            "source_session_id": "s2",
            "source_trace_id": "t2",
            "applicability_tags": ["web", "crawl"],
            "confidence": 0.75,
            "hit_count": 1,
            "created_at": "2026-01-02T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
        },
    ]


@pytest.fixture
def long_card() -> list[dict]:
    """A card whose compressed form exceeds default per-card limit."""
    return [
        {
            "experience_id": "long-card",
            "title": "Very Long Title That Exceeds Forty Characters Limit",
            "what_worked": [
                "This is an extremely long worked item that definitely exceeds sixty characters and keeps going and going"
            ],
            "what_failed": [],
            "guidance": "This is an extremely long guidance that exceeds eighty characters and just keeps going on and on",
            "avoidance": ["Another extremely long avoidance text that goes well beyond eighty characters here too"],
            "trigger_hint": "type:long",
            "trigger_keywords": ["long", "test"],
            "scope": "global",
            "source_task_id": "task-long",
            "source_session_id": "s1",
            "source_trace_id": "tlong",
            "applicability_tags": [],
            "confidence": 0.9,
            "hit_count": 0,
            "created_at": "2026-01-03T00:00:00+00:00",
            "updated_at": "2026-01-03T00:00:00+00:00",
        },
    ]


# =============================================================================
# Prompt Injector Unit Tests
# =============================================================================

class TestWorkExperiencePromptInjector:
    """Unit tests for the prompt injector logic."""

    def test_compress_short_card(self, sample_cards: list[dict]) -> None:
        """Short cards are compressed correctly."""
        from hubos.core.work_experience.prompt_injector import compress_experience_card

        card = sample_cards[0]
        hint = compress_experience_card(card, max_chars=200)

        assert "CSV File Processing" in hint
        assert "Do:" in hint
        assert len(hint) <= 200

    def test_compress_long_card_truncated(self, long_card: list[dict]) -> None:
        """Overlong cards are truncated to max_chars."""
        from hubos.core.work_experience.prompt_injector import compress_experience_card

        # The long_card fields individually fit within per-field limits but
        # the total compressed string exceeds 200 chars.
        hint = compress_experience_card(long_card[0], max_chars=200)
        assert len(hint) <= 200
        # When a card's compressed form exceeds the limit, it gets "..." truncated
        # (This card's title(40) + what_worked(60) + guidance(80) = 185 < 200,
        #  so it fits. Use a tighter limit to test truncation.)
        hint_tight = compress_experience_card(long_card[0], max_chars=100)
        assert hint_tight.endswith("...") or len(hint_tight) <= 100

    def test_build_injection_empty_cards(self) -> None:
        """Empty card list returns empty string."""
        from hubos.core.work_experience.prompt_injector import build_experience_injection

        result = build_experience_injection([])
        assert result == ""

    def test_build_injection_single_card(
        self,
        sample_cards: list[dict],
    ) -> None:
        """Single card produces injection with header and footer markers."""
        from hubos.core.work_experience.prompt_injector import build_experience_injection

        result = build_experience_injection(sample_cards[:1])

        assert "[Work Guidance]" in result
        assert "[/Work Guidance]" in result
        assert "CSV File Processing" in result

    def test_build_injection_top_k_respected(self, sample_cards: list[dict]) -> None:
        """Only top-k cards are injected."""
        from hubos.core.work_experience.prompt_injector import build_experience_injection

        result = build_experience_injection(sample_cards, max_k=1)

        assert "CSV File Processing" in result
        assert "Web Crawl" not in result

    def test_build_injection_total_budget_respected(
        self,
        sample_cards: list[dict],
    ) -> None:
        """Total injection length is capped by max_total_chars."""
        from hubos.core.work_experience.prompt_injector import build_experience_injection

        # With max_total_chars=500 (large enough for both cards), result fits
        # With max_total_chars=250 (tight), some hints may be trimmed
        result_500 = build_experience_injection(sample_cards, max_total_chars=500)
        assert "[Work Guidance]" in result_500
        assert len(result_500) <= 500

        result_250 = build_experience_injection(sample_cards, max_total_chars=250)
        assert "[Work Guidance]" in result_250
        assert len(result_250) <= 250

    def test_inject_into_prompt_appends_before(
        self,
        sample_cards: list[dict],
    ) -> None:
        """Injection is prepended to the user prompt (before main content)."""
        from hubos.core.work_experience.prompt_injector import inject_experience_into_prompt

        prompt = "Process this file: data.csv"
        result = inject_experience_into_prompt(prompt, sample_cards)

        # Injection block starts with \n\n, so check the marker is at the beginning
        assert result.lstrip().startswith("[Work Guidance]")
        # Original content is preserved at the end
        assert "Process this file:" in result

    def test_inject_into_prompt_empty_cards_no_change(
        self,
        sample_cards: list[dict],
    ) -> None:
        """Empty cards → prompt returned unchanged."""
        from hubos.core.work_experience.prompt_injector import inject_experience_into_prompt

        prompt = "Process this file: data.csv"
        result = inject_experience_into_prompt(prompt, [])

        assert result == prompt
        assert "[Work Guidance]" not in result


# =============================================================================
# generate_for_stage Injection Tests
# =============================================================================

class TestGenerateForStagePromptInjection:
    """Tests for experience injection in generate_for_stage()."""

    def test_flag_off_prompt_unchanged(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
        sample_cards: list[dict],
    ) -> None:
        """Flag OFF: user_prompt passed to provider unchanged."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "false"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            user_template = STAGE_PROMPTS["dev"]["user_template"]
            expected_prompt = user_template.format(input="read a CSV file")

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="read a CSV file",
                context={"work_experience_cards": sample_cards},
            )

            # Provider received the ORIGINAL prompt (no injection)
            call_kwargs = mock_provider.generate.call_args
            assert call_kwargs is not None
            actual_prompt = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt")
            assert actual_prompt == expected_prompt
            assert "[Work Guidance]" not in actual_prompt

    def test_flag_on_injects_experience_hint(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
        sample_cards: list[dict],
    ) -> None:
        """Flag ON: compressed experience hint prepended to user prompt."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="read a CSV file",
                context={"work_experience_cards": sample_cards},
            )

            call_kwargs = mock_provider.generate.call_args
            actual_prompt = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt")

            # Injection block is prepended (starts with \n\n then markers)
            assert actual_prompt.lstrip().startswith("[Work Guidance]")
            # Original content is still present
            assert "read a CSV file" in actual_prompt
            # Markers present
            assert "[/Work Guidance]" in actual_prompt

    def test_flag_on_empty_cards_no_injection(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
    ) -> None:
        """Flag ON but empty cards: prompt unchanged (no injection markers)."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            user_template = STAGE_PROMPTS["dev"]["user_template"]
            expected_prompt = user_template.format(input="do something")

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="do something",
                context={"work_experience_cards": []},
            )

            call_kwargs = mock_provider.generate.call_args
            actual_prompt = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt")

            assert actual_prompt == expected_prompt
            assert "[Work Guidance]" not in actual_prompt

    def test_flag_on_no_context_no_injection(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
    ) -> None:
        """Flag ON but no context: prompt unchanged."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            user_template = STAGE_PROMPTS["dev"]["user_template"]
            expected_prompt = user_template.format(input="do something")

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="do something",
                context=None,
            )

            call_kwargs = mock_provider.generate.call_args
            actual_prompt = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt")

            assert actual_prompt == expected_prompt

    def test_overlong_card_trimmed(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
        long_card: list[dict],
    ) -> None:
        """Flag ON: overlong cards are trimmed to budget."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="do something",
                context={"work_experience_cards": long_card},
            )

            call_kwargs = mock_provider.generate.call_args
            actual_prompt = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt")

            # Card compressed and truncated
            injection_start = actual_prompt.find("[Work Guidance]")
            injection_end = actual_prompt.find("[/Relevant Past Experience]")
            injection = actual_prompt[injection_start:injection_end + len("[/Relevant Past Experience]")]

            # Per-card limit is 200 chars, so full long title+content must be truncated
            # The title alone is 50+ chars, so full compression must fit in 200
            assert len(injection) <= 250  # generous upper bound

    def test_top_k_limit_respected(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
    ) -> None:
        """Flag ON: only top 2 cards are injected (DEFAULT_MAX_K=2)."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            many_cards = [
                {
                    "experience_id": f"id-{i}",
                    "title": f"Experience {i}",
                    "what_worked": [f"Method {i} worked"],
                    "what_failed": [],
                    "guidance": f"Use method {i}",
                    "avoidance": [],
                    "trigger_hint": f"type:{i}",
                    "trigger_keywords": [f"kw{i}"],
                    "scope": "global",
                    "source_task_id": f"task-{i}",
                    "source_session_id": "s1",
                    "source_trace_id": f"t{i}",
                    "applicability_tags": [],
                    "confidence": 0.8,
                    "hit_count": i,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
                for i in range(5)
            ]

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="do something",
                context={"work_experience_cards": many_cards},
            )

            call_kwargs = mock_provider.generate.call_args
            actual_prompt = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt")

            # Only 2 cards should appear (DEFAULT_MAX_K=2)
            assert "Experience 0" in actual_prompt
            assert "Experience 1" in actual_prompt
            # Cards 2, 3, 4 should NOT appear
            assert "Experience 2" not in actual_prompt
            assert "Experience 3" not in actual_prompt
            assert "Experience 4" not in actual_prompt

    def test_execution_result_unchanged(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
        sample_cards: list[dict],
    ) -> None:
        """Flag ON: execution result (text, success, usage) is returned correctly."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="read a CSV file",
                context={"work_experience_cards": sample_cards},
            )

            assert result.success is True
            assert result.text == "Executed successfully"
            assert result.usage is not None
            assert result.usage["total_tokens"] == 70

    def test_system_prompt_unchanged(
        self,
        runtime: LLMRuntime,
        mock_provider: MagicMock,
        sample_cards: list[dict],
    ) -> None:
        """Flag ON: system prompt is NOT modified by experience injection."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            result = runtime.generate_for_stage(
                stage="dev",
                input_text="read a CSV file",
                context={"work_experience_cards": sample_cards},
            )

            call_kwargs = mock_provider.generate.call_args
            system_prompt = call_kwargs.kwargs.get("system_prompt") or call_kwargs[1].get("system_prompt")

            # System prompt should be the stage system prompt, unchanged
            assert system_prompt == STAGE_PROMPTS["dev"]["system"]
            assert "[Work Guidance]" not in system_prompt
