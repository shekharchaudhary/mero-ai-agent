"""CI entrypoint for eval-driven gating.

Patterns (see ../SKILL.md):
  - Compares to a PINNED baseline (a specific past run), not a rolling average.
  - Per-tag gating with regression-tag absolute zero-tolerance.
  - N-seed judge runs with median, for robustness against noise.
  - Stratified sample for PR runs; full set for nightly/manual.
  - Dollar cap per run with abort + partial-results.
  - Caches per-case results by (prompt_hash, tools_hash, model, case_id).
  - Emits a single JSON result file the PR-comment generator consumes.
  - Non-zero exit code on hard regressions; the CI workflow re-asserts.

Wire your own `agent_fn` and `judge_fn` in via the AgentRunner protocol below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("eval_ci")

# --- Hard-coded defaults that match SKILL.md guidance ---
DEFAULT_GATES = {
    "regression_case_absolute": 0,       # any regression-tag drop blocks
    "adversarial_aggregate_drop": 0.0,
    "happy_path_aggregate_drop": 0.02,
    "edge_case_aggregate_drop": 0.05,    # warn-only
    "overall_aggregate_drop": 0.02,      # warn-only
    "cost_per_case_increase": 0.25,      # warn-only
    "latency_p95_increase": 0.50,        # warn-only
}
WARN_ONLY_GATES = {
    "edge_case_aggregate_drop",
    "overall_aggregate_drop",
    "cost_per_case_increase",
    "latency_p95_increase",
}


class AgentRunner(Protocol):
    def run(self, case_input: Any) -> tuple[str, float, int]: ...
    # returns: (output, cost_usd, latency_ms)


class Judge(Protocol):
    def score(self, case: dict[str, Any], output: str) -> float: ...


# --- Data model ---

@dataclass
class CaseResult:
    id: str
    tags: list[str]
    score: float
    cost_usd: float
    latency_ms: int
    seeds_used: int = 1


@dataclass
class GateOutcome:
    name: str
    threshold: float | int
    actual: float | int
    passed: bool
    warn_only: bool
    note: str = ""


@dataclass
class RunResult:
    run_id: str
    code_sha: str
    prompt_hash: str
    tools_hash: str
    model_id: str
    eval_set_version: str
    baseline_run_id: str | None
    started_at: float
    elapsed_s: float
    aggregate_score: float
    per_tag: dict[str, float]
    per_case: list[CaseResult]
    cost_usd: float
    latency_p95_ms: int
    gates: list[GateOutcome]
    aborted_for_cost: bool = False

    def has_hard_failure(self) -> bool:
        return any(not g.passed and not g.warn_only for g in self.gates)


# --- The runner ---

def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return float(s[k])


def _load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        log.warning("no baseline at %s — first run, no gating", path)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _stratified_sample(cases: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """Always include regression-tagged cases; sample the rest by tag.

    Targets ~50 cases for a PR run regardless of eval set size.
    """
    must_keep = [c for c in cases if "regression" in c.get("tags", [])]
    rest = [c for c in cases if "regression" not in c.get("tags", [])]
    by_primary: dict[str, list[dict[str, Any]]] = {}
    for c in rest:
        primary = next((t for t in c.get("tags", []) if t in {"happy-path", "edge-case", "adversarial"}), "untagged")
        by_primary.setdefault(primary, []).append(c)
    sample: list[dict[str, Any]] = list(must_keep)
    target_per_tag = max(5, (50 - len(must_keep)) // max(len(by_primary), 1))
    for tag, group in by_primary.items():
        sample.extend(rng.sample(group, min(target_per_tag, len(group))))
    return sample


def _cache_key(prompt_hash: str, tools_hash: str, model_id: str, case_id: str, seeds: int) -> str:
    h = hashlib.sha1()
    h.update(f"{prompt_hash}|{tools_hash}|{model_id}|{case_id}|{seeds}".encode())
    return h.hexdigest()[:16]


def run_case(
    case: dict[str, Any],
    agent: AgentRunner,
    judge: Judge | None,
    seeds: int,
    cache_dir: Path | None,
    cache_key_args: tuple[str, str, str],
) -> CaseResult:
    if cache_dir is not None:
        ck = _cache_key(*cache_key_args, case["id"], seeds)
        cache_file = cache_dir / f"{ck}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return CaseResult(**cached)

    seed_scores: list[float] = []
    total_cost = 0.0
    total_latency = 0
    for _ in range(seeds):
        output, cost, latency = agent.run(case["input"])
        total_cost += cost
        total_latency += latency
        if judge is not None:
            seed_scores.append(judge.score(case, output))
        else:
            seed_scores.append(1.0 if case.get("expected", "") == output else 0.0)

    result = CaseResult(
        id=case["id"],
        tags=case.get("tags", []),
        score=statistics.median(seed_scores),
        cost_usd=round(total_cost, 6),
        latency_ms=int(total_latency / seeds),
        seeds_used=seeds,
    )
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(asdict(result)), encoding="utf-8")  # type: ignore[possibly-undefined]
    return result


def aggregate(results: list[CaseResult]) -> tuple[float, dict[str, float]]:
    if not results:
        return 0.0, {}
    overall = sum(r.score for r in results) / len(results)
    by_tag: dict[str, list[float]] = {}
    for r in results:
        for t in r.tags:
            by_tag.setdefault(t, []).append(r.score)
    per_tag = {t: sum(s) / len(s) for t, s in by_tag.items()}
    return overall, per_tag


def evaluate_gates(
    new: RunResult, baseline: dict[str, Any] | None
) -> list[GateOutcome]:
    if baseline is None:
        return []   # no baseline, no gates

    gates: list[GateOutcome] = []

    def gate(name: str, threshold: float | int, actual: float | int, passed: bool, note: str = "") -> None:
        gates.append(GateOutcome(
            name=name, threshold=threshold, actual=actual,
            passed=passed, warn_only=name in WARN_ONLY_GATES, note=note,
        ))

    base_per_case = {c["id"]: c["score"] for c in baseline.get("per_case", [])}
    regression_drops = [
        c.id for c in new.per_case
        if "regression" in c.tags
        and c.id in base_per_case
        and c.score < base_per_case[c.id]
    ]
    gate(
        "regression_case_absolute",
        DEFAULT_GATES["regression_case_absolute"],
        len(regression_drops),
        len(regression_drops) <= DEFAULT_GATES["regression_case_absolute"],
        note=", ".join(regression_drops[:5]),
    )

    for tag, gate_name in [
        ("adversarial", "adversarial_aggregate_drop"),
        ("happy-path", "happy_path_aggregate_drop"),
        ("edge-case", "edge_case_aggregate_drop"),
    ]:
        base = baseline.get("per_tag", {}).get(tag)
        nv = new.per_tag.get(tag)
        if base is None or nv is None:
            continue
        drop = base - nv
        gate(gate_name, DEFAULT_GATES[gate_name], round(drop, 4),
             drop <= DEFAULT_GATES[gate_name])

    base_overall = baseline.get("aggregate_score")
    if base_overall is not None:
        drop = base_overall - new.aggregate_score
        gate("overall_aggregate_drop", DEFAULT_GATES["overall_aggregate_drop"],
             round(drop, 4), drop <= DEFAULT_GATES["overall_aggregate_drop"])

    base_cost = baseline.get("cost_per_case")
    if base_cost and base_cost > 0:
        new_cpc = new.cost_usd / max(len(new.per_case), 1)
        increase = (new_cpc - base_cost) / base_cost
        gate("cost_per_case_increase", DEFAULT_GATES["cost_per_case_increase"],
             round(increase, 4), increase <= DEFAULT_GATES["cost_per_case_increase"])

    base_lat = baseline.get("latency_p95_ms")
    if base_lat and base_lat > 0:
        inc = (new.latency_p95_ms - base_lat) / base_lat
        gate("latency_p95_increase", DEFAULT_GATES["latency_p95_increase"],
             round(inc, 4), inc <= DEFAULT_GATES["latency_p95_increase"])

    return gates


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--eval-set", required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--results-dir", required=True)
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--model", required=True)
    p.add_argument("--sample-policy", default="full", choices=["full", "stratified"])
    p.add_argument("--judge-seeds", type=int, default=3)
    p.add_argument("--dollar-cap", type=float, default=5.0)
    p.add_argument("--code-sha", required=True)
    p.add_argument("--prompt-hash", required=True)
    p.add_argument("--tools-hash", required=True)
    p.add_argument("--eval-set-version", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    # Load cases (JSONL files under eval-set/)
    cases: list[dict[str, Any]] = []
    for jsonl in sorted(Path(args.eval_set).rglob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append(json.loads(line))

    rng = random.Random(args.eval_set_version)   # deterministic per eval-set
    if args.sample_policy == "stratified":
        cases = _stratified_sample(cases, rng)

    baseline = _load_baseline(Path(args.baseline))

    # The agent + judge are project-specific. To make this template runnable
    # without external dependencies, we use placeholders the caller overrides.
    from importlib import import_module
    project = import_module(os.environ.get("EVAL_PROJECT_MODULE", "evals.ci.project"))
    agent: AgentRunner = project.get_agent(args.model)         # type: ignore[attr-defined]
    judge: Judge | None = getattr(project, "get_judge", lambda: None)()

    results: list[CaseResult] = []
    started = time.monotonic()
    total_cost = 0.0
    aborted = False
    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    for case in cases:
        if total_cost >= args.dollar_cap:
            log.warning("dollar cap reached at $%.4f — aborting with partial results", total_cost)
            aborted = True
            break
        r = run_case(
            case, agent, judge, args.judge_seeds, cache_dir,
            cache_key_args=(args.prompt_hash, args.tools_hash, args.model),
        )
        results.append(r)
        total_cost += r.cost_usd

    overall, per_tag = aggregate(results)
    p95 = int(_percentile([r.latency_ms for r in results], 95))

    run = RunResult(
        run_id=hashlib.sha1(f"{args.code_sha}|{args.prompt_hash}|{time.time()}".encode()).hexdigest()[:12],
        code_sha=args.code_sha,
        prompt_hash=args.prompt_hash,
        tools_hash=args.tools_hash,
        model_id=args.model,
        eval_set_version=args.eval_set_version,
        baseline_run_id=(baseline or {}).get("run_id"),
        started_at=started,
        elapsed_s=round(time.monotonic() - started, 2),
        aggregate_score=round(overall, 4),
        per_tag={t: round(s, 4) for t, s in sorted(per_tag.items())},
        per_case=results,
        cost_usd=round(total_cost, 4),
        latency_p95_ms=p95,
        gates=[],
        aborted_for_cost=aborted,
    )
    run.gates = evaluate_gates(run, baseline)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(run), default=str, indent=2), encoding="utf-8")

    log.info("aggregate=%.4f cost=$%.4f gates_failed_hard=%d",
             run.aggregate_score, run.cost_usd,
             sum(1 for g in run.gates if not g.passed and not g.warn_only))
    return 1 if run.has_hard_failure() else 0


if __name__ == "__main__":
    sys.exit(main())
