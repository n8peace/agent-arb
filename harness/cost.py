"""Token accounting and the hard budget for paid runs. Standard library only.

One CostTracker is shared by every provider in a run. Providers call check_before() ahead of
each API call and record() after it. Spend is computed from the token counts the API reports
(response.usage), priced from config.PRICES_USD_PER_MTOK.
"""
from typing import Dict, List, Optional

from . import config


class BudgetExceeded(Exception):
    """Raised before a call that could push the actual spend past the --max-usd limit."""


def price_for(model: str) -> Dict[str, float]:
    try:
        return config.PRICES_USD_PER_MTOK[model]
    except KeyError:
        raise ValueError("no price for model %r in config.PRICES_USD_PER_MTOK" % model)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p = price_for(model)
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1e6


def estimate_tokens(system: str, messages: List[dict]) -> int:
    """Rough prompt size: config.CHARS_PER_TOKEN characters per token, rounded up."""
    chars = len(system) + sum(len(m["content"]) for m in messages)
    return -(-chars // config.CHARS_PER_TOKEN)


class CostTracker:
    def __init__(self, max_usd: Optional[float] = None):
        self.max_usd = max_usd
        self.spent_usd = 0.0
        self.calls = 0
        self.malformed = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.by_model: Dict[str, dict] = {}
        self.stop_reason: Optional[str] = None   # set when a run ends early

    def snapshot(self) -> dict:
        return {"inputTokens": self.input_tokens, "outputTokens": self.output_tokens,
                "costUsd": self.spent_usd}

    def since(self, snap: dict) -> dict:
        return {"inputTokens": self.input_tokens - snap["inputTokens"],
                "outputTokens": self.output_tokens - snap["outputTokens"],
                "costUsd": round(self.spent_usd - snap["costUsd"], 6)}

    def check_before(self, model: str, system: str, messages: List[dict], max_tokens: int) -> None:
        """Stop before a call whose worst case (estimated prompt + the full max_tokens reply)
        would push the actual spend so far past the limit."""
        if self.max_usd is None:
            return
        worst = cost_usd(model, estimate_tokens(system, messages), max_tokens)
        if self.spent_usd + worst > self.max_usd:
            raise BudgetExceeded(
                "budget stop: spent $%.4f, and the next %s call could cost up to $%.4f, "
                "which would pass the $%.2f limit" % (self.spent_usd, model, worst, self.max_usd))

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.spent_usd += cost_usd(model, input_tokens, output_tokens)
        m = self.by_model.setdefault(model, {"calls": 0, "inputTokens": 0, "outputTokens": 0, "costUsd": 0.0})
        m["calls"] += 1
        m["inputTokens"] += input_tokens
        m["outputTokens"] += output_tokens
        m["costUsd"] += cost_usd(model, input_tokens, output_tokens)

    def report(self) -> dict:
        return {
            "maxUsd": self.max_usd,
            "spentUsd": round(self.spent_usd, 6),
            "calls": self.calls,
            "malformedReplies": self.malformed,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "byModel": {k: dict(v, costUsd=round(v["costUsd"], 6)) for k, v in self.by_model.items()},
            "stoppedEarly": self.stop_reason,
        }
