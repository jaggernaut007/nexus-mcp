"""Agent-tier choices: which runner and model, which conditions, which preset.

A spec has the form ``runner[:model]``, for example ``claude``, ``claude:haiku`` or
``codex:gpt-5-codex``. Prices here are rough estimates for the cost gate before a run.
The Claude CLI reports the real cost of each run. Codex cost comes from token usage
and ``PRICES``. Override the prices with a JSON file in ``NEXUS_EVAL_PRICING``.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

RUNNERS = ("claude", "codex", "agy")
DEFAULT_MODEL = {
    "claude": "sonnet",
    "codex": "gpt-5-codex",
    "agy": "gemini-3.8-flash-medium",
}
CONDITIONS = ("baseline", "mcp", "nexus")

# USD per run, for the estimate shown before any money is spent.
RUN_ESTIMATE_USD = {"haiku": 0.04, "sonnet": 0.15, "opus": 0.45, "fable": 0.60}
CODEX_RUN_ESTIMATE_USD = 0.12
# Antigravity (agy) reports tokens, not USD. This is a rough guide for the cost gate only.
AGY_RUN_ESTIMATE_USD = 0.05
# USD per million tokens (input, cached input, output), for Codex runs only.
PRICES: Dict[str, Dict[str, float]] = {
    "gpt-5-codex": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "gpt-5": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.0},
}

PRESETS = {
    "quick": {"specs": ["claude:sonnet"], "conditions": ["baseline", "nexus"], "tasks": 4},
    "matrix": {"specs": None, "conditions": ["baseline", "nexus"], "tasks": 6},
    "efficient": {"specs": None, "conditions": ["baseline", "mcp", "nexus"], "tasks": 6},
}


@dataclass(frozen=True)
class Spec:
    runner: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.runner}:{self.model}"


def parse_specs(text: str) -> List[Spec]:
    """Parse ``claude:haiku,codex`` into specs. Raises ValueError for an unknown runner."""
    specs: List[Spec] = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        runner, _, model = part.partition(":")
        if runner not in RUNNERS:
            raise ValueError(f"Unknown runner '{runner}'. Use one of: {', '.join(RUNNERS)}")
        spec = Spec(runner, model or DEFAULT_MODEL[runner])
        if spec not in specs:
            specs.append(spec)
    if not specs:
        raise ValueError("Give at least one runner, for example --agent claude")
    return specs


def parse_conditions(text: str) -> List[str]:
    names = [c.strip() for c in text.split(",") if c.strip()]
    bad = [c for c in names if c not in CONDITIONS]
    if bad or not names:
        raise ValueError(f"Conditions must be from: {', '.join(CONDITIONS)}")
    return names


def prices() -> Dict[str, Dict[str, float]]:
    """Codex prices, with the user's override file merged on top."""
    merged = dict(PRICES)
    override = os.environ.get("NEXUS_EVAL_PRICING")
    if override:
        with open(override) as handle:
            merged.update(json.load(handle))
    return merged


def run_estimate(spec: Spec) -> float:
    if spec.runner == "codex":
        return CODEX_RUN_ESTIMATE_USD
    if spec.runner == "agy":
        return AGY_RUN_ESTIMATE_USD
    for family, usd in RUN_ESTIMATE_USD.items():
        if family in spec.model:
            return usd
    return RUN_ESTIMATE_USD["sonnet"]


def codex_cost(model: str, usage: Dict[str, int]) -> Optional[float]:
    """USD for one Codex run from its token usage, or None for an unpriced model."""
    table = prices().get(model)
    if not table:
        return None
    cached = usage.get("cached_input_tokens", 0)
    fresh = max(usage.get("input_tokens", 0) - cached, 0)
    usd = (
        fresh * table["input"]
        + cached * table["cached_input"]
        + usage.get("output_tokens", 0) * table["output"]
    )
    return round(usd / 1_000_000, 5)
