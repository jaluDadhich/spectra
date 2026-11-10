"""Token/cost accounting and per-case budget enforcement (spec 6.3)."""
from __future__ import annotations

from typing import Optional

from app.schemas.review import TokenUsage


class BudgetExceeded(Exception):
    pass


class CostTracker:
    def __init__(self, max_calls: int, budget_usd: float,
                 price_in_per_1k: Optional[float], price_out_per_1k: Optional[float],
                 reasoning_price_in_per_1k: Optional[float] = None,
                 reasoning_price_out_per_1k: Optional[float] = None):
        self.max_calls = max_calls
        self.budget_usd = budget_usd
        self.price_in = price_in_per_1k
        self.price_out = price_out_per_1k
        self.reasoning_price_in = reasoning_price_in_per_1k
        self.reasoning_price_out = reasoning_price_out_per_1k
        self.usage = TokenUsage()

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> Optional[float]:
        if self.price_in is None or self.price_out is None:
            return None
        return (input_tokens / 1000.0) * self.price_in + (output_tokens / 1000.0) * self.price_out

    def check_budget(self) -> None:
        if self.usage.calls >= self.max_calls:
            raise BudgetExceeded(
                f"Per-case LLM call limit reached ({self.max_calls})")
        if self.usage.estimated_cost_usd is not None and \
                self.usage.estimated_cost_usd >= self.budget_usd:
            raise BudgetExceeded(
                f"Per-case LLM budget reached (${self.budget_usd:.2f})")

    def record(self, input_tokens: int, output_tokens: int,
               retries: int = 0, error: bool = False,
               model: Optional[str] = None, cost_tier: str = "fast") -> None:
        self.usage.calls += 1
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        self.usage.retries += retries
        if error:
            self.usage.errors += 1
        is_reasoning = cost_tier == "reasoning"
        if is_reasoning and self.reasoning_price_in is not None and self.reasoning_price_out is not None:
            cost = (input_tokens / 1000.0) * self.reasoning_price_in + \
                (output_tokens / 1000.0) * self.reasoning_price_out
        else:
            cost = self.estimate_cost(input_tokens, output_tokens)
        if cost is not None:
            self.usage.estimated_cost_usd = round(
                (self.usage.estimated_cost_usd or 0.0) + cost, 6)
