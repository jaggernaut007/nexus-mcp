"""Tests for token budget management."""

import pytest

from nexus_mcp.formatting.token_budget import TokenBudget


class TestEstimation:
    def test_estimate_tokens(self):
        budget = TokenBudget("detailed")
        assert budget.estimate_tokens("abcd") == 1
        assert budget.estimate_tokens("abcdefgh") == 2

    def test_estimate_empty(self):
        budget = TokenBudget("detailed")
        assert budget.estimate_tokens("") == 0


class TestFits:
    def test_fits_under_budget(self):
        budget = TokenBudget("summary")
        assert budget.fits("x" * 1999) is True

    def test_fits_at_budget(self):
        budget = TokenBudget("summary")
        assert budget.fits("x" * 2000) is True

    def test_does_not_fit_over_budget(self):
        budget = TokenBudget("summary")
        assert budget.fits("x" * 2001) is False


class TestTruncation:
    def test_truncate_short_text(self):
        budget = TokenBudget("summary")
        text = "short text"
        assert budget.truncate(text) == text

    def test_truncate_long_text(self):
        budget = TokenBudget("summary")  # 2000 chars
        text = "word " * 500  # 2500 chars
        result = budget.truncate(text)
        assert len(result) <= 2003  # budget + "..."
        assert result.endswith("...")

    def test_truncate_with_reserve(self):
        budget = TokenBudget("summary")
        text = "x" * 2000
        result = budget.truncate(text, reserve=1500)
        assert len(result) <= 503  # 500 + "..."

    def test_truncate_zero_budget(self):
        budget = TokenBudget("summary")
        result = budget.truncate("text", reserve=3000)
        assert result == ""


class TestVerbosityLevels:
    def test_summary_budget(self):
        budget = TokenBudget("summary")
        assert budget.budget_chars == 2000

    def test_detailed_budget(self):
        budget = TokenBudget("detailed")
        assert budget.budget_chars == 8000

    def test_full_budget(self):
        budget = TokenBudget("full")
        assert budget.budget_chars == 32000

    def test_invalid_verbosity(self):
        with pytest.raises(ValueError, match="verbosity must be one of"):
            TokenBudget("invalid")

    def test_budgets_ordered(self):
        s = TokenBudget("summary")
        d = TokenBudget("detailed")
        f = TokenBudget("full")
        assert s.budget_chars < d.budget_chars < f.budget_chars


class TestRemaining:
    def test_remaining_positive(self):
        budget = TokenBudget("summary")
        assert budget.remaining(1000) == 1000

    def test_remaining_zero(self):
        budget = TokenBudget("summary")
        assert budget.remaining(2000) == 0

    def test_remaining_negative_clamps(self):
        budget = TokenBudget("summary")
        assert budget.remaining(3000) == 0
