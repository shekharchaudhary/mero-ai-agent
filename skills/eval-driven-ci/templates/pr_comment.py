"""Render the PR-comment markdown from an eval_ci JSON result.

Patterns (see ../SKILL.md):
  - Headline first: pass/fail + aggregate delta. The 15-second read.
  - Per-tag table with gate status.
  - Top-5 case diffs by absolute score change.
  - Cost + latency deltas as their own lines.
  - Run metadata footer with the deployment tuple for traceability.

Usage:
    python pr_comment.py path/to/pr.json
    # prints the comment body to stdout, ready for `gh pr comment --body`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

WARN_ONLY_GATES = {
    "edge_case_aggregate_drop",
    "overall_aggregate_drop",
    "cost_per_case_increase",
    "latency_p95_increase",
}


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}pp"


def _money(x: float) -> str:
    return f"${x:.4f}"


def _gate_icon(g: dict[str, Any]) -> str:
    if g["passed"]:
        return "OK"
    return "WARN" if g["warn_only"] else "FAIL"


def render(result: dict[str, Any]) -> str:
    hard_fail = any(not g["passed"] and not g["warn_only"] for g in result.get("gates", []))
    baseline_run = result.get("baseline_run_id") or "(none)"

    base_aggregate: float | None = None
    for g in result.get("gates", []):
        if g["name"] == "overall_aggregate_drop":
            base_aggregate = round(result["aggregate_score"] + g["actual"], 4)
            break

    if base_aggregate is None:
        headline = (
            f"**EVALS {'FAILED' if hard_fail else 'PASSED'}** · "
            f"aggregate {result['aggregate_score']:.4f} · "
            f"first run vs baseline {baseline_run}"
        )
    else:
        delta = result["aggregate_score"] - base_aggregate
        headline = (
            f"**EVALS {'FAILED' if hard_fail else 'PASSED'}** · "
            f"{_pct(delta)} vs baseline {baseline_run} · "
            f"{base_aggregate:.4f} → {result['aggregate_score']:.4f}"
        )

    lines: list[str] = [headline, ""]

    if result.get("aborted_for_cost"):
        lines.append("> Partial results: dollar cap reached during run.")
        lines.append("")

    # Per-tag table.
    per_tag = result.get("per_tag", {})
    lines.append("### Per-tag")
    lines.append("| Tag | Score | Gate |")
    lines.append("| --- | --- | --- |")
    for tag in sorted(per_tag.keys()):
        # Only match aggregate-drop gates by tag prefix; count-based gates
        # (e.g. regression_case_absolute) are surfaced in the Failing-gates
        # section instead.
        expected_name = f"{tag.replace('-', '_')}_aggregate_drop"
        gate_row = ""
        for g in result.get("gates", []):
            if g["name"] == expected_name:
                gate_row = f"{_gate_icon(g)} (Δ {_pct(-g['actual'])}, threshold {_pct(-g['threshold'])})"
                break
        lines.append(f"| `{tag}` | {per_tag[tag]:.4f} | {gate_row or '—'} |")
    lines.append("")

    # Top-5 case diffs by absolute score change.
    cases = sorted(
        result.get("per_case", []),
        key=lambda c: abs(c.get("score_delta", 0.0)),
        reverse=True,
    )[:5]
    if cases:
        lines.append("### Top case changes")
        lines.append("| Case | Tags | Score |")
        lines.append("| --- | --- | --- |")
        for c in cases:
            tags = ",".join(c.get("tags", []))
            lines.append(f"| `{c['id']}` | {tags} | {c['score']:.3f} |")
        lines.append("")

    # Cost + latency.
    cost_line = f"- Total cost: {_money(result.get('cost_usd', 0.0))}"
    n = max(1, len(result.get("per_case", [])))
    cost_line += f" ({_money(result.get('cost_usd', 0.0) / n)} per case)"
    lines.append(cost_line)
    lines.append(f"- Latency p95: {result.get('latency_p95_ms', 0)} ms")
    lines.append(f"- Elapsed: {result.get('elapsed_s', 0):.1f}s")
    lines.append("")

    # Failing gates summary (above the metadata so it isn't missed).
    failing = [g for g in result.get("gates", []) if not g["passed"]]
    if failing:
        lines.append("### Failing gates")
        for g in failing:
            lines.append(
                f"- {_gate_icon(g)} `{g['name']}` — actual {g['actual']}, "
                f"threshold {g['threshold']}{(' — ' + g['note']) if g.get('note') else ''}"
            )
        lines.append("")

    # Metadata footer.
    lines.append("---")
    lines.append(
        f"<sub>run `{result.get('run_id')}` · "
        f"code `{(result.get('code_sha') or '')[:12]}` · "
        f"prompt `{(result.get('prompt_hash') or '')[:12]}` · "
        f"tools `{(result.get('tools_hash') or '')[:12]}` · "
        f"model `{result.get('model_id')}` · "
        f"eval-set `{(result.get('eval_set_version') or '')[:12]}`</sub>"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: pr_comment.py <eval-result.json>", file=sys.stderr)
        return 2
    data = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
