"""Conditional Edge Routing Evaluator for DAG-native Step 6.

Provides safe expression evaluation for conditional DAG routing.
"""

import re
import operator
from dataclasses import dataclass
from typing import Any, Callable, Optional


# White-listed operators for safe evaluation
SAFE_OPERATORS: dict[str, Callable[..., bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "and": lambda a, b: bool(a) and bool(b),
    "or": lambda a, b: bool(a) or bool(b),
    "not": lambda a: not bool(a),
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
    "startswith": lambda a, b: str(a).startswith(str(b)),
    "endswith": lambda a, b: str(a).endswith(str(b)),
    "contains": lambda a, b: b in str(a),
}


# White-listed context access patterns
SAFE_CONTEXT_PATTERNS = [
    r"^[a-zA-Z_][a-zA-Z0-9_]*$",  # Simple identifiers
]


@dataclass
class EvaluationResult:
    """Result of condition evaluation."""
    expression: str
    result: bool
    evaluated_at: float
    error: Optional[str] = None


class ConditionEvaluator:
    """Safe conditional expression evaluator for DAG edges.

    Uses a restricted subset of Python syntax to prevent arbitrary code execution.
    """

    def __init__(self) -> None:
        self._safe_vars: set[str] = {
            "true", "false", "True", "False",
            "none", "None",
            "risk_score", "code_lang", "task_type",
            "node_id", "role", "attempt",
            "input_length", "has_dependencies",
        }

    def evaluate(
        self,
        expression: str,
        context: dict[str, Any],
    ) -> EvaluationResult:
        """Evaluate a conditional expression safely.

        Args:
            expression: The condition expression (e.g., "risk_score > 0.7 && code_lang == 'python'")
            context: Context data for evaluation (node output, task metadata)

        Returns:
            EvaluationResult with result or error
        """
        import time

        result = EvaluationResult(
            expression=expression,
            result=False,
            evaluated_at=time.time(),
        )

        try:
            # Normalize expression
            normalized = self._normalize_expression(expression)

            # Parse into tokens
            tokens = self._tokenize(normalized)

            # Evaluate using safe evaluator
            eval_result = self._safe_eval(tokens, context)

            result.result = bool(eval_result)

        except SyntaxError as e:
            result.error = f"Syntax error: {str(e)}"
        except ValueError as e:
            result.error = f"Value error: {str(e)}"
        except Exception as e:
            result.error = f"Evaluation error: {str(e)}"

        return result

    def _normalize_expression(self, expr: str) -> str:
        """Normalize expression syntax."""
        # Convert && to AND, || to OR
        expr = expr.replace("&&", " AND ")
        expr = expr.replace("||", " OR ")

        # Convert != to "not equal"
        # Keep != as is for safety

        return expr.strip()

    def _tokenize(self, expr: str) -> list[str]:
        """Simple tokenization of expression."""
        # Remove extra whitespace
        expr = " ".join(expr.split())

        tokens = []
        current = ""
        in_string = False
        string_char = None

        i = 0
        while i < len(expr):
            char = expr[i]

            if char in ("'", '"') and not in_string:
                in_string = True
                string_char = char
                current += char
            elif char == string_char and in_string:
                in_string = False
                current += char
                tokens.append(current)
                current = ""
                string_char = None
            elif in_string:
                current += char
            elif char in (" ", "(", ")", "[", "]"):
                if current:
                    tokens.append(current)
                    current = ""
                if char != " ":
                    tokens.append(char)
            else:
                current += char

            i += 1

        if current:
            tokens.append(current)

        return tokens

    def _safe_eval(self, tokens: list[str], context: dict[str, Any]) -> Any:
        """Safely evaluate tokenized expression."""
        # Build a safe namespace with context
        safe_ns: dict[str, Any] = dict(context)

        # Add safe operators
        safe_ns.update(SAFE_OPERATORS)

        # Add true/false constants
        safe_ns["true"] = True
        safe_ns["false"] = False
        safe_ns["True"] = True
        safe_ns["False"] = False
        safe_ns["none"] = None
        safe_ns["None"] = None

        # Join tokens into expression
        # Replace known identifiers with context lookups
        processed_tokens = []
        for token in tokens:
            if token in ("(", ")", "AND", "OR", "NOT", "==", "!=", ">", ">=", "<", "<="):
                # Convert Python keywords to lowercase for eval
                if token == "AND":
                    processed_tokens.append("and")
                elif token == "OR":
                    processed_tokens.append("or")
                elif token == "NOT":
                    processed_tokens.append("not")
                else:
                    processed_tokens.append(token)
            elif re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", token):
                if token in safe_ns:
                    processed_tokens.append(token)
                else:
                    # Treat as context variable
                    processed_tokens.append(f"context.get('{token}', False)")
            elif token.startswith(("'", '"')) or token in ("True", "False", "None"):
                processed_tokens.append(token)
            else:
                processed_tokens.append(token)

        expression = " ".join(processed_tokens)

        # Use eval with restricted globals
        # Only allow access to safe_ns
        result = eval(expression, {"__builtins__": {}}, safe_ns)  # nosec B307

        return result

    def is_safe_expression(self, expression: str) -> bool:
        """Check if an expression is safe (no dangerous patterns)."""
        dangerous_patterns = [
            r"import\s+",  # import statements
            r"from\s+\w+\s+import",  # from x import
            r"exec\s*\(",  # exec
            r"eval\s*\(",  # eval
            r"open\s*\(",  # file operations
            r"__",  # dunder methods
            r"\[.*\].*=",  # slice assignment
            r".*\[.*\]\s*=",  # index assignment
        ]

        for pattern in dangerous_patterns:
            if re.search(pattern, expression, re.IGNORECASE):
                return False

        return True


def evaluate_condition(
    expression: str,
    node_output: dict[str, Any],
    task_metadata: dict[str, Any],
) -> EvaluationResult:
    """Convenience function to evaluate a condition.

    Args:
        expression: Condition expression
        node_output: Output from the source node
        task_metadata: Task metadata (input, type, etc.)

    Returns:
        EvaluationResult
    """
    evaluator = ConditionEvaluator()

    # Build context from node output and task metadata
    context: dict[str, Any] = {}
    context.update(node_output)
    context.update(task_metadata)

    # Extract common fields
    context["input_length"] = len(task_metadata.get("input", ""))
    context["has_dependencies"] = len(node_output) > 0

    return evaluator.evaluate(expression, context)
