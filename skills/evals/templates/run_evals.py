"""Minimal eval runner.

Loads a JSONL eval set, runs each case through a user-supplied agent
function, scores with a mix of deterministic and (optionally) LLM-judge
scorers, and writes a results JSON with per-case and per-tag breakdowns.

Usage:
    from run_evals import run_evals

    def my_agent(prompt: str) -> str:
        return client.messages.create(...).content[0].text

    results = run_evals(
        evalset_path="evals/eval/evalset.jsonl",
        agent=my_agent,
        results_dir="evals/results",
        run_id="2026-05-18-claude-opus-4-7",
    )
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class CaseResult:
    id: str
    tags: list[str]
    output: str
    score: float
    checks: dict[str, bool]
    latency_ms: int
    error: str | None = None


@dataclass
class RunResult:
    run_id: str
    model: str
    eval_set_hash: str
    aggregate_score: float
    per_tag: dict[str, float]
    cases: list[CaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "model": self.model,
            "eval_set_hash": self.eval_set_hash,
            "aggregate_score": self.aggregate_score,
            "per_tag": self.per_tag,
            "cases": [c.__dict__ for c in self.cases],
        }


# --- Deterministic scorers ---

def _check_exact(output: str, expected: str) -> bool:
    return output.strip() == expected.strip()


def _check_contains(output: str, needles: list[str]) -> bool:
    low = output.lower()
    return all(n.lower() in low for n in needles)


def _check_contains_any(output: str, needles: list[str]) -> bool:
    low = output.lower()
    return any(n.lower() in low for n in needles)


def _check_not_contains(output: str, needles: list[str]) -> bool:
    low = output.lower()
    return not any(n.lower() in low for n in needles)


def _check_max_sentences(output: str, n: int) -> bool:
    return len(re.findall(r"[.!?]+", output.strip())) <= n


def _check_refuses(output: str) -> bool:
    return any(p in output.lower() for p in ["cannot", "can't", "unable", "won't", "decline"])


def _check_refuses_or_clarifies(output: str) -> bool:
    return _check_refuses(output) or "?" in output


def score_case(output: str, expected: dict[str, Any]) -> tuple[float, dict[str, bool]]:
    """Run every check key present in `expected`. Score is the fraction that pass."""
    checks: dict[str, bool] = {}
    if "exact" in expected:
        checks["exact"] = _check_exact(output, expected["exact"])
    if "contains" in expected:
        checks["contains"] = _check_contains(output, expected["contains"])
    if "contains_any" in expected:
        checks["contains_any"] = _check_contains_any(output, expected["contains_any"])
    if "not_contains" in expected:
        checks["not_contains"] = _check_not_contains(output, expected["not_contains"])
    if "max_sentences" in expected:
        checks["max_sentences"] = _check_max_sentences(output, expected["max_sentences"])
    if "refuses" in expected and expected["refuses"]:
        checks["refuses"] = _check_refuses(output)
    if "refuses_or_clarifies" in expected and expected["refuses_or_clarifies"]:
        checks["refuses_or_clarifies"] = _check_refuses_or_clarifies(output)
    if not checks:
        return 0.0, {"no_checks_defined": False}
    return sum(checks.values()) / len(checks), checks


def run_evals(
    evalset_path: str | Path,
    agent: Callable[[str], str],
    results_dir: str | Path,
    run_id: str,
    model: str = "unknown",
) -> RunResult:
    evalset_path = Path(evalset_path)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    raw = evalset_path.read_bytes()
    eval_set_hash = hashlib.sha256(raw).hexdigest()[:12]

    cases: list[CaseResult] = []
    per_tag_sum: dict[str, float] = defaultdict(float)
    per_tag_count: dict[str, int] = defaultdict(int)

    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        t0 = time.time()
        try:
            output = agent(case["input"])
            error = None
        except Exception as e:
            output = ""
            error = f"{type(e).__name__}: {e}"
        latency_ms = int((time.time() - t0) * 1000)

        if error:
            score, checks = 0.0, {"ran": False}
        else:
            score, checks = score_case(output, case["expected"])

        result = CaseResult(
            id=case["id"],
            tags=case.get("tags", []),
            output=output,
            score=score,
            checks=checks,
            latency_ms=latency_ms,
            error=error,
        )
        cases.append(result)
        for tag in result.tags:
            per_tag_sum[tag] += score
            per_tag_count[tag] += 1

    aggregate = sum(c.score for c in cases) / max(len(cases), 1)
    per_tag = {t: per_tag_sum[t] / per_tag_count[t] for t in per_tag_sum}

    result = RunResult(
        run_id=run_id,
        model=model,
        eval_set_hash=eval_set_hash,
        aggregate_score=round(aggregate, 4),
        per_tag={t: round(s, 4) for t, s in sorted(per_tag.items())},
        cases=cases,
    )
    out_path = results_dir / f"{run_id}.json"
    out_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    def echo_agent(prompt: str) -> str:
        return prompt

    r = run_evals(
        evalset_path=Path(__file__).parent / "evalset.example.jsonl",
        agent=echo_agent,
        results_dir=Path(__file__).parent / "_results",
        run_id="demo",
        model="echo",
    )
    print(json.dumps({"aggregate": r.aggregate_score, "per_tag": r.per_tag}, indent=2))
