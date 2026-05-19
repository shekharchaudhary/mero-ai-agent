"""Orchestrator + workers, async, with budget propagation and caps.

Patterns (see ../SKILL.md):
  - Subagents dispatched in parallel via asyncio.gather.
  - Per-worker timeout + per-worker budget slice (parent's remaining - reserve).
  - Hard depth and fan-out caps from config, not from prompt.
  - Partial results accepted — failed/timed-out workers reported, run continues.
  - Trace context (trace_id, parent_span_id) propagated into every subagent.
  - Output envelope mirrors the tool-design contract: ok / summary / data / error.

This is a runtime — wire in your own subagent implementations behind
`Worker.run`. The orchestrator itself is model-agnostic; it coordinates.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

log = logging.getLogger(__name__)

Outcome = Literal["success", "failure", "timeout", "budget_exhausted"]


@dataclass(frozen=True)
class OrchestratorConfig:
    max_depth: int = 3
    max_fanout: int = 10
    worker_timeout_s: float = 60.0
    run_wall_clock_s: float = 300.0
    run_cost_usd_cap: float = 5.0
    aggregation_reserve_frac: float = 0.15  # reserve of parent budget for the final aggregate call


@dataclass
class RunBudget:
    started_at: float = field(default_factory=time.monotonic)
    cost_usd: float = 0.0
    wall_clock_cap_s: float = 300.0
    cost_cap_usd: float = 5.0

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.wall_clock_cap_s - (time.monotonic() - self.started_at))

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cost_cap_usd - self.cost_usd)

    def exhausted(self) -> bool:
        return self.remaining_s <= 0 or self.remaining_usd <= 0


@dataclass
class TraceContext:
    trace_id: str
    parent_span_id: str | None = None
    depth: int = 0

    def child(self) -> "TraceContext":
        return TraceContext(
            trace_id=self.trace_id,
            parent_span_id=uuid.uuid4().hex[:12],
            depth=self.depth + 1,
        )


@dataclass
class WorkerResult:
    ok: bool
    outcome: Outcome
    summary: str
    data: dict[str, Any] | None = None
    cost_usd: float = 0.0
    span_id: str | None = None
    error: str | None = None


# A worker is anything that, given typed inputs + a budget + trace, returns a
# WorkerResult. Implementations call models, tools, RAG, etc.
Worker = Callable[[dict[str, Any], RunBudget, TraceContext], Awaitable[WorkerResult]]
Aggregator = Callable[[list[WorkerResult], RunBudget, TraceContext], Awaitable[WorkerResult]]


class DepthExceeded(Exception): ...
class FanoutExceeded(Exception): ...


async def dispatch(
    subtasks: list[dict[str, Any]],
    *,
    worker: Worker,
    aggregator: Aggregator,
    budget: RunBudget,
    trace: TraceContext,
    config: OrchestratorConfig = OrchestratorConfig(),
) -> WorkerResult:
    """Fan out subtasks to workers, then aggregate. Returns a WorkerResult envelope."""
    if trace.depth >= config.max_depth:
        raise DepthExceeded(
            f"Refusing to dispatch at depth {trace.depth} (max_depth={config.max_depth})"
        )
    if len(subtasks) > config.max_fanout:
        raise FanoutExceeded(
            f"Refusing fanout of {len(subtasks)} (max_fanout={config.max_fanout})"
        )
    if budget.exhausted():
        return WorkerResult(
            ok=False, outcome="budget_exhausted",
            summary="Parent budget exhausted before dispatch.",
        )

    reserve = config.aggregation_reserve_frac
    per_worker_remaining = budget.remaining_s * (1 - reserve) / max(len(subtasks), 1)
    per_worker_timeout = min(config.worker_timeout_s, per_worker_remaining)

    log.info(
        "orchestrator_dispatch",
        extra={
            "trace_id": trace.trace_id,
            "depth": trace.depth,
            "fanout": len(subtasks),
            "per_worker_timeout_s": round(per_worker_timeout, 2),
            "budget_remaining_s": round(budget.remaining_s, 2),
            "budget_remaining_usd": round(budget.remaining_usd, 4),
        },
    )

    async def _one(task: dict[str, Any]) -> WorkerResult:
        child_trace = trace.child()
        child_budget = RunBudget(
            wall_clock_cap_s=per_worker_timeout,
            cost_cap_usd=budget.remaining_usd * (1 - reserve) / max(len(subtasks), 1),
        )
        try:
            return await asyncio.wait_for(
                worker(task, child_budget, child_trace),
                timeout=per_worker_timeout,
            )
        except asyncio.TimeoutError:
            return WorkerResult(
                ok=False, outcome="timeout",
                summary=f"Worker exceeded {per_worker_timeout:.1f}s",
                span_id=child_trace.parent_span_id,
            )
        except Exception as e:
            return WorkerResult(
                ok=False, outcome="failure",
                summary=f"Worker raised {type(e).__name__}",
                error=str(e)[:200],
                span_id=child_trace.parent_span_id,
            )

    results = await asyncio.gather(*[_one(t) for t in subtasks])

    # Roll up subagent cost into the parent budget.
    for r in results:
        budget.cost_usd += r.cost_usd

    successes = [r for r in results if r.ok]
    failures = [r for r in results if not r.ok]
    log.info(
        "orchestrator_collected",
        extra={
            "trace_id": trace.trace_id,
            "ok": len(successes),
            "failed": len(failures),
            "cost_usd_running": round(budget.cost_usd, 4),
        },
    )

    if not successes:
        return WorkerResult(
            ok=False,
            outcome="failure",
            summary=f"All {len(results)} workers failed.",
            data={"failures": [r.summary for r in failures]},
        )

    return await aggregator(results, budget, trace)


# --- Example: trivial worker + aggregator ---


async def example_worker(task: dict[str, Any], budget: RunBudget, trace: TraceContext) -> WorkerResult:
    """Stand-in worker: pretends to do work proportional to task['effort_s']."""
    effort = float(task.get("effort_s", 0.1))
    await asyncio.sleep(min(effort, budget.remaining_s))
    return WorkerResult(
        ok=True,
        outcome="success",
        summary=f"Processed {task.get('id', '?')}",
        data={"task_id": task.get("id"), "effort_s": effort},
        cost_usd=effort * 0.001,
        span_id=trace.parent_span_id,
    )


async def example_aggregator(
    results: list[WorkerResult], budget: RunBudget, trace: TraceContext
) -> WorkerResult:
    ok = [r for r in results if r.ok]
    return WorkerResult(
        ok=bool(ok),
        outcome="success" if ok else "failure",
        summary=f"Aggregated {len(ok)}/{len(results)} successful results.",
        data={"items": [r.data for r in ok]},
        cost_usd=0.0,
        span_id=trace.parent_span_id,
    )


async def _demo() -> None:
    trace = TraceContext(trace_id=uuid.uuid4().hex[:12])
    budget = RunBudget(wall_clock_cap_s=10.0, cost_cap_usd=1.0)
    result = await dispatch(
        subtasks=[{"id": i, "effort_s": 0.05 * i} for i in range(1, 5)],
        worker=example_worker,
        aggregator=example_aggregator,
        budget=budget,
        trace=trace,
    )
    print({
        "ok": result.ok,
        "outcome": result.outcome,
        "summary": result.summary,
        "items": len(result.data["items"]) if result.data else 0,
        "total_cost_usd": round(budget.cost_usd, 4),
    })


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(_demo())
