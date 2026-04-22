"""Test user facing response sanitizer - Step 9.1.

Tests that the sanitizer removes internal headers like
## 战略分析, 用户意图, 关键考量, etc.
"""

import pytest
from hubos.core.llm.runtime import LLMRuntime
from hubos.core.llm.providers.minimax_provider import MiniMaxProvider


class TestUserFacingResponseSanitizer:
    """Tests for user-facing response sanitizer."""

    def test_removes_strategic_analysis_headers(self):
        """Test that ## 战略分析 is removed."""
        runtime = LLMRuntime()

        dirty = """
## 战略分析
这是内部战略分析内容。
## 用户意图
用户想知道AI是什么
## 关键考量
需要简洁回答
用户面向的答复应该是这样的。
"""
        clean = runtime._clean_review_output(dirty)

        assert "## 战略分析" not in clean
        assert "## 用户意图" not in clean
        assert "## 关键考量" not in clean
        assert "用户面向的答复应该是这样的" in clean

    def test_removes_thinking_tags(self):
        """Test that <think>...</think> tags are removed."""
        runtime = LLMRuntime()

        dirty = "<think>用户问我是谁，我需要简短回答。用户面向的答复应该是这样的。</think>用户面向的答复应该是这样的。"
        clean = runtime._clean_review_output(dirty)

        assert "<think>" not in clean
        assert "</think>" not in clean
        assert "用户面向的答复应该是这样的" in clean

    def test_removes_leading_stage_labels(self):
        """Test that [CEO], [INFO], [DEV], [REVIEW] labels are removed."""
        runtime = LLMRuntime()

        dirty = """[REVIEW]
[REVIEW] 用户面向的答复应该是这样的。
"""
        clean = runtime._clean_review_output(dirty)

        assert "[REVIEW]" not in clean
        assert "用户面向的答复应该是这样的" in clean

    def test_removes_markdown_headers(self):
        """Test that markdown headers are removed."""
        runtime = LLMRuntime()

        dirty = """## 内部标题
# 另一个标题
用户面向的答复应该是这样的。
"""
        clean = runtime._clean_review_output(dirty)

        assert "## 内部标题" not in clean
        assert "# 另一个标题" not in clean
        assert "用户面向的答复应该是这样的" in clean

    def test_preserves_code_blocks(self):
        """Test that code blocks in answers are preserved."""
        runtime = LLMRuntime()

        dirty = """
用户面向的答复：
```python
def hello():
    print("hello")
```
这是代码后面的自然语言。
"""
        clean = runtime._clean_review_output(dirty)

        assert "```python" in clean
        assert "def hello()" in clean

    def test_preserves_natural_chinese_answer(self):
        """Test that a clean Chinese answer is preserved."""
        runtime = LLMRuntime()

        clean_input = "你好！我是AI助手，可以帮你回答问题、写代码、分析数据。有什么需要帮忙的吗？"
        result = runtime._clean_review_output(clean_input)

        assert result == clean_input


class TestReviewPromptStrict:
    """Tests that review prompt is strict enough."""

    def test_review_prompt_mentions_no_thinking_tags(self):
        """Verify review prompt explicitly forbids thinking tags."""
        from hubos.core.llm.runtime import STAGE_PROMPTS

        review_prompt = STAGE_PROMPTS["review"]["system"]

        # Should mention thinking tags
        assert "<think>" in review_prompt or "thinking" in review_prompt.lower()

        # Should mention no headers
        assert "##" in review_prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
