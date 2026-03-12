"""Token budget management for response size control.

Provides char-based token estimation and truncation to keep
responses within context window limits.
"""


class TokenBudget:
    """Estimate and enforce token budgets using char-based approximation.

    Approximation: 1 token ~ 4 characters (reasonable for English/code).
    """

    CHARS_PER_TOKEN = 4

    BUDGETS = {
        "summary": 2000,     # ~500 tokens
        "detailed": 8000,    # ~2000 tokens
        "full": 32000,       # ~8000 tokens
    }

    def __init__(self, verbosity: str = "detailed"):
        if verbosity not in self.BUDGETS:
            raise ValueError(
                f"verbosity must be one of {list(self.BUDGETS.keys())}, got '{verbosity}'"
            )
        self.verbosity = verbosity
        self.budget_chars = self.BUDGETS[verbosity]

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a string."""
        return len(text) // self.CHARS_PER_TOKEN

    def fits(self, text: str) -> bool:
        """Check if text fits within the budget."""
        return len(text) <= self.budget_chars

    def remaining(self, used_chars: int) -> int:
        """Return remaining character budget after used_chars."""
        return max(0, self.budget_chars - used_chars)

    def truncate(self, text: str, reserve: int = 0) -> str:
        """Truncate text to fit within budget minus reserve.

        Truncates at word boundary and appends '...' if truncated.
        """
        limit = self.budget_chars - reserve
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text

        # Truncate at last space before limit
        truncated = text[:limit]
        last_space = truncated.rfind(" ")
        if last_space > limit // 2:
            truncated = truncated[:last_space]

        return truncated.rstrip() + "..."
