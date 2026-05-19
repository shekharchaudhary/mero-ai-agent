"""Append-only cost ledger with per-call attribution and pre-call cap enforcement.

Patterns (see ../SKILL.md):
  - Six required fields per entry; refuse to record an entry missing any.
  - Cost computed at write time using a versioned pricing table.
  - Pre-call check: estimate cost from token-count pre-pass; reject if it
    would push the tenant or run over a cap.
  - Cache hit rate and cost-per-outcome are derived views, not stored numbers.

Storage here is a JSONL file for clarity. In production, replace `_persist`
with a write to a durable store (object storage, KV, OLTP) and run the
writer async.

Usage:
    from cost_ledger import CostLedger, BudgetCap, BudgetExceeded
    from pricing import Usage

    ledger = CostLedger(
        path="/var/agent/cost.jsonl",
        run_cap_usd=1.0,
        tenant_daily_cap_usd=5.0,
        tenant_monthly_cap_usd=100.0,
    )
    ledger.precheck(tenant_id="t-1", trace_id="r-1", estimated_cost_usd=0.02)
    # ... make the model call ...
    ledger.record(
        tenant_id="t-1", trace_id="r-1", user_id="u-9",
        usage=Usage(model_id="claude-opus-4-7", input_tokens=2000, output_tokens=500),
        feature="code-review",
        outcome=None,                 # set on run completion via finalize_run()
    )
    ledger.finalize_run(trace_id="r-1", outcome="success")
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pricing import Usage, cost_usd_for

log = logging.getLogger(__name__)

Outcome = Literal["success", "failure", "partial", "user_aborted", "budget_exhausted"]


@dataclass(frozen=True)
class BudgetCap:
    run_usd: float
    tenant_daily_usd: float
    tenant_monthly_usd: float


class BudgetExceeded(Exception):
    """Raised when a precheck would push a budget over its cap."""

    def __init__(self, scope: str, current: float, attempted: float, cap: float) -> None:
        self.scope = scope
        self.current = current
        self.attempted = attempted
        self.cap = cap
        super().__init__(
            f"{scope}_budget_exceeded: current=${current:.4f} + attempted=${attempted:.4f} > cap=${cap:.4f}"
        )


@dataclass
class _RunState:
    tenant_id: str
    cost_usd: float = 0.0
    outcome: Outcome | None = None
    entries: int = 0


@dataclass
class CostLedger:
    path: str
    run_cap_usd: float = 1.0
    tenant_daily_cap_usd: float = 5.0
    tenant_monthly_cap_usd: float = 100.0

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _runs: dict[str, _RunState] = field(default_factory=dict, init=False, repr=False)
    _tenant_daily: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float), init=False, repr=False)
    _tenant_monthly: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float), init=False, repr=False)

    def precheck(self, tenant_id: str, trace_id: str, estimated_cost_usd: float) -> None:
        """Raise BudgetExceeded if recording this would breach any cap."""
        with self._lock:
            run = self._runs.get(trace_id)
            run_now = run.cost_usd if run else 0.0
            if run_now + estimated_cost_usd > self.run_cap_usd:
                raise BudgetExceeded("run", run_now, estimated_cost_usd, self.run_cap_usd)

            today, month = self._date_keys()
            day_now = self._tenant_daily[(tenant_id, today)]
            if day_now + estimated_cost_usd > self.tenant_daily_cap_usd:
                raise BudgetExceeded("tenant_daily", day_now, estimated_cost_usd, self.tenant_daily_cap_usd)
            mon_now = self._tenant_monthly[(tenant_id, month)]
            if mon_now + estimated_cost_usd > self.tenant_monthly_cap_usd:
                raise BudgetExceeded("tenant_monthly", mon_now, estimated_cost_usd, self.tenant_monthly_cap_usd)

    def record(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        user_id: str,
        usage: Usage,
        feature: str,
        outcome: Outcome | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, object]:
        cost, price_version = cost_usd_for(usage)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "user_id": user_id,
            "model_id": usage.model_id,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_tokens": usage.cache_creation_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_ttl": usage.cache_ttl,
            "feature": feature,
            "outcome": outcome,
            "latency_ms": latency_ms,
            "cost_usd": cost,
            "price_version": price_version,
        }
        with self._lock:
            run = self._runs.setdefault(trace_id, _RunState(tenant_id=tenant_id))
            run.cost_usd += cost
            run.entries += 1

            today, month = self._date_keys(entry["ts"])
            self._tenant_daily[(tenant_id, today)] += cost
            self._tenant_monthly[(tenant_id, month)] += cost
            self._persist(entry)
        return entry

    def finalize_run(self, trace_id: str, outcome: Outcome) -> _RunState | None:
        with self._lock:
            run = self._runs.get(trace_id)
            if run is None:
                return None
            run.outcome = outcome
            self._persist({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "run_complete",
                "trace_id": trace_id,
                "tenant_id": run.tenant_id,
                "total_cost_usd": round(run.cost_usd, 6),
                "entries": run.entries,
                "outcome": outcome,
            })
            return run

    # --- Derived views ---

    def cache_hit_rate(self, since_ts: float | None = None) -> float:
        reads = creates = inputs = 0
        for e in self._read_entries(since_ts):
            reads += int(e.get("cache_read_tokens", 0) or 0)
            creates += int(e.get("cache_creation_tokens", 0) or 0)
            inputs += int(e.get("input_tokens", 0) or 0)
        denom = reads + creates + inputs
        return reads / denom if denom else 0.0

    def cost_per_outcome(self, feature: str) -> dict[str, float]:
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for e in self._read_entries():
            if e.get("event") != "run_complete":
                continue
            if e.get("feature") and e["feature"] != feature:
                continue
            outcome = e.get("outcome") or "unknown"
            sums[outcome] += float(e.get("total_cost_usd", 0.0))
            counts[outcome] += 1
        return {o: sums[o] / counts[o] for o in sums if counts[o]}

    # --- Internals ---

    def _date_keys(self, iso_ts: str | None = None) -> tuple[str, str]:
        ts = datetime.fromisoformat(iso_ts) if iso_ts else datetime.now(timezone.utc)
        return ts.strftime("%Y-%m-%d"), ts.strftime("%Y-%m")

    def _persist(self, entry: dict[str, object]) -> None:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _read_entries(self, since_ts: float | None = None) -> list[dict[str, object]]:
        path = Path(self.path)
        if not path.exists():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts is not None:
                ts = e.get("ts")
                if ts and datetime.fromisoformat(str(ts)).timestamp() < since_ts:
                    continue
            entries.append(e)
        return entries


if __name__ == "__main__":
    # Smoke test: record one call, finalize, print derived views.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        ledger = CostLedger(path=tmp.name, run_cap_usd=1.0)
        ledger.precheck(tenant_id="t-1", trace_id="r-1", estimated_cost_usd=0.001)
        ledger.record(
            tenant_id="t-1", trace_id="r-1", user_id="u-9",
            usage=Usage(
                model_id="claude-opus-4-7",
                input_tokens=2_000, output_tokens=500,
                cache_creation_tokens=10_000, cache_read_tokens=8_000,
            ),
            feature="code-review",
            latency_ms=2400,
        )
        run = ledger.finalize_run("r-1", outcome="success")
        print(json.dumps({
            "total_cost_usd": round(run.cost_usd, 6) if run else None,
            "cache_hit_rate": round(ledger.cache_hit_rate(), 3),
            "cost_per_outcome[code-review]": ledger.cost_per_outcome("code-review"),
        }, indent=2))
