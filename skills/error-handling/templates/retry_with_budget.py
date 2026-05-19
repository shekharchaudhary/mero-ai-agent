"""Retry helper with full-jitter backoff and explicit budgets.

Three caps any retry call site should respect:
  1. max_attempts        — hard ceiling on tries
  2. max_wall_clock_s    — total wall-clock spent across all attempts
  3. max_cost_usd        — total cost (sum of per-attempt costs) — opt-in

When any cap is exhausted, stop and raise. Never retry past a budget.

Usage:
    from retry_with_budget import retry, Budget, transient

    @retry(Budget(max_attempts=4, max_wall_clock_s=30), is_retryable=transient)
    def call_model(prompt: str) -> str:
        ...
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Budget:
    max_attempts: int = 4
    max_wall_clock_s: float = 30.0
    max_cost_usd: float | None = None
    base_delay_s: float = 0.5
    max_delay_s: float = 30.0


@dataclass
class _Outcome:
    attempts: int = 0
    elapsed_s: float = 0.0
    cost_usd: float = 0.0
    last_error: Exception | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class BudgetExhausted(Exception):
    """Raised when a retry budget is exhausted. Wraps the last underlying error."""

    def __init__(self, outcome: _Outcome) -> None:
        self.outcome = outcome
        last = outcome.last_error
        msg = (
            f"Retry budget exhausted after {outcome.attempts} attempts, "
            f"{outcome.elapsed_s:.1f}s, ${outcome.cost_usd:.4f}. "
            f"Last error: {type(last).__name__ if last else 'none'}: {last}"
        )
        super().__init__(msg)


def transient(exc: Exception) -> bool:
    """Default retryable classifier — adjust per project.

    Retry on connection/timeout-shaped errors. Do NOT retry on
    ValueError, KeyError, or anything that signals invalid input.
    """
    name = type(exc).__name__
    if name in {"ValueError", "KeyError", "TypeError", "AttributeError"}:
        return False
    msg = str(exc).lower()
    retryable_signals = (
        "timeout", "timed out", "temporarily", "rate limit", "rate-limited",
        "overloaded", "unavailable", "connection reset", "broken pipe",
        "502", "503", "504", "429",
    )
    return any(s in msg or s in name.lower() for s in retryable_signals)


def _full_jitter(attempt: int, base: float, cap: float) -> float:
    return random.uniform(0, min(cap, base * (2 ** attempt)))


def retry(
    budget: Budget,
    is_retryable: Callable[[Exception], bool] = transient,
    cost_of: Callable[[Any], float] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator. Wraps a callable with bounded, classified retries.

    `cost_of(result)` is an optional hook returning the dollar cost of a
    completed attempt — used to enforce `max_cost_usd`. Called only on success.
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            outcome = _Outcome()
            start = time.monotonic()
            while True:
                outcome.attempts += 1
                try:
                    result = fn(*args, **kwargs)
                    if cost_of is not None:
                        outcome.cost_usd += cost_of(result)
                    outcome.elapsed_s = time.monotonic() - start
                    log.info(
                        "retry_success",
                        extra={
                            "attempts": outcome.attempts,
                            "elapsed_s": round(outcome.elapsed_s, 3),
                            "cost_usd": round(outcome.cost_usd, 4),
                        },
                    )
                    return result
                except Exception as e:
                    outcome.last_error = e
                    outcome.elapsed_s = time.monotonic() - start
                    outcome.history.append(
                        {"attempt": outcome.attempts, "error_type": type(e).__name__, "msg": str(e)[:200]}
                    )
                    if not is_retryable(e):
                        log.warning(
                            "retry_giveup_nonretryable",
                            extra={"attempts": outcome.attempts, "error_type": type(e).__name__},
                        )
                        raise
                    if outcome.attempts >= budget.max_attempts:
                        raise BudgetExhausted(outcome) from e
                    if outcome.elapsed_s >= budget.max_wall_clock_s:
                        raise BudgetExhausted(outcome) from e
                    if budget.max_cost_usd is not None and outcome.cost_usd >= budget.max_cost_usd:
                        raise BudgetExhausted(outcome) from e
                    delay = _full_jitter(outcome.attempts - 1, budget.base_delay_s, budget.max_delay_s)
                    remaining = budget.max_wall_clock_s - outcome.elapsed_s
                    delay = min(delay, max(0.0, remaining))
                    log.info(
                        "retry_attempt_failed",
                        extra={
                            "attempt": outcome.attempts,
                            "next_delay_s": round(delay, 3),
                            "error_type": type(e).__name__,
                        },
                    )
                    time.sleep(delay)

        return wrapper

    return deco
