---
name: eval-driven-ci
description: Scaffold CI integration for evals — score gating on PRs, per-tag tolerance, selective runs on changed prompt/tool/model paths, regression bisection by prompt hash, judge-noise handling, and cost caps per CI run. Use when evals already exist but nothing automatically catches a regression before merge.
---

# eval-driven-ci

Building an eval set is the first half of the work. Wiring it into CI so a regression blocks the merge is the second half — and the part most projects skip. This skill turns the `evals` skill from "you can run them" into "they run automatically, gate every change that could affect behavior, and tell you exactly which case broke." Pairs with `evals` (the underlying suite this extends), `prompt-engineering` (prompt hashes are the unit of bisection), `cost-tracking` (CI eval runs go in the same ledger as production), and `deployment` (the eval baseline ID is part of the deployment tuple).

## When to trigger

- User types `/eval-driven-ci`.
- The `evals` skill is already set up but nobody runs the suite consistently.
- User says "we shipped a regression nobody caught" / "evals only get run when someone remembers" / "I want a number on every PR."
- An existing CI runs evals but doesn't gate, doesn't compare to a baseline, or runs the whole suite on every commit at unreasonable cost.

## Pre-requisites this skill assumes

Don't run this skill until these are in place:

- A working eval set with the dev / eval split from the `evals` skill.
- Deterministic scorers wherever possible; LLM judges only where nothing else fits.
- Cases tagged at minimum with `happy-path` / `edge-case` / `adversarial` / `regression`.
- A way to render the prompt to a stable hash (prompt versioning from `prompt-engineering`).

If any of these is missing, fix it first. CI on a fragile eval set just makes flakiness automated.

## Gate on per-tag deltas, not the aggregate alone

The aggregate score is the worst place to gate. It averages out the cases that actually matter.

The minimum gate set:

| Gate | Default threshold | Severity |
| --- | --- | --- |
| Any drop on a `regression`-tagged case | `1 case` | **Block merge** — every regression case represents a fixed bug. |
| `adversarial` aggregate drop | `> 0pp` | **Block merge** — adversarial cases are safety, not quality. |
| `happy-path` aggregate drop | `> 2pp` (noise floor) | **Block merge.** |
| `edge-case` aggregate drop | `> 5pp` | **Warn**, do not block (these are noisier). |
| Overall aggregate drop | `> 2pp` | **Warn**, do not block — the per-tag gates above are stronger signals. |
| Cost per case increase | `> 25%` | **Warn.** Catches accidental cache breakage. |
| Latency p95 regression | `> 50%` | **Warn.** |

Set these as **defaults**, not the law. The right thresholds depend on judge noise (calibrate; see below) and the project's risk tolerance.

## Run on changed paths, not every commit

The CI cost of running a full eval suite on every commit is real, and most commits don't change behavior. Trigger evals selectively:

- **Behavior-affecting paths.** Any change to prompts, tool definitions, model config, or the agent loop code → full suite.
- **Code-only changes.** Library updates, refactors that don't touch the above → skip evals; rely on the standard test suite.
- **Eval-set changes.** A change to the eval set itself → run the *new* suite against the *current* baseline to establish a new baseline; do not gate.
- **Schedule.** Nightly run of the full suite against `main` regardless of changes — catches drift from model-side updates that bypass your PRs.

Implement with path filters in the CI config. The full suite must still be runnable on demand (label, manual dispatch) for cases where the heuristic misses.

## Baseline: pin a run, not an average

A floating "rolling average" baseline drifts and hides slow regressions. Pin a baseline to a specific eval run ID:

- Store the baseline as `evals/baseline.json` (or equivalent): the run ID, the per-case scores, the prompt/model/tools hashes, the date.
- Compare every PR run to **that** baseline. Per-case diffs become first-class output.
- Refresh the baseline on an explicit step: when prompts intentionally change, when the model upgrades, when the eval set is refreshed. Refreshing is a deliberate PR, not an automatic update.
- Keep the **old baselines** in `evals/baselines/`. Historical comparison ("how did v5 do on the case that broke in v7?") is impossible without them.

## Judge noise and the "3 seeds" rule

LLM-judge scorers are noisy. A single-seed run will fluctuate enough to trip naïve gates 5-15% of the time even with no behavior change.

Robustness rules:

- **Run judges N≥3 times per case** and take the median. For high-stakes gates, N=5.
- **Determine your noise floor empirically.** Run the same prompt against the same baseline 10 times; the spread defines your noise threshold. Gate thresholds should be ≥2× that spread.
- **Use deterministic scorers when possible.** They have zero noise. Reserve judges for cases where nothing else fits.
- **Block-list flaky cases until they're either deterministic or stable.** A case that fails 30% of repeats with no code change isn't measuring what you think.

This isn't optional. CI that fires on noise teaches the team to override it; then the gate is decoration.

## Regression bisection

When a gate fails, the value of CI is whether the team can find the cause in under an hour.

- **Stamp every CI eval run** with: code SHA, prompt hash, tools hash, model ID, eval-set version. The deployment tuple from the `deployment` skill applies here too.
- **Surface a per-case diff** on the PR: which cases changed, by how much, with the old and new outputs side by side.
- **Bisect by prompt hash, not commit.** When the prompt is the suspect, the prompt-hash CHANGELOG gives a faster bisect than `git bisect`.
- **Cache CI results by `(prompt_hash, model_id, eval_set_version)`.** Re-running the same configuration shouldn't re-pay for the eval. If a PR doesn't touch behavior, the cache hits, the score is immediate.

## Cost controls

CI evals against an LLM judge can cost real money fast. Two PR comments per minute on a big repo can compound to surprising bills.

- **Per-run dollar cap.** Abort partial-result with the partial score reported if exceeded. Never silently keep spending.
- **Sample large eval sets in PR runs.** Stratify by tag; run all `regression`-tagged cases plus a sample of the rest. Nightly runs use the full set.
- **Re-use cached scores** for cases whose inputs and the agent build (prompt+model+tools) haven't changed.
- **Skip CI evals on draft PRs.** Add a label like `run-evals` for PRs that need them mid-draft.

The bill is also a signal: if CI eval cost is climbing, look for cache misses (something is invalidating the prefix on every run) before raising the cap.

## What the PR comment must show

The PR comment is the only artifact most reviewers will read. Optimize it for "is this a regression, and where?":

1. **Headline**: pass/fail + aggregate delta vs baseline (e.g. `EVALS FAILED · -3.2pp vs baseline #129`).
2. **Per-tag table**: tag, old score, new score, delta, gate status.
3. **Top 5 case diffs**: case ID, old score, new score, brief diff snippet of the output change.
4. **Cost + latency deltas** as separate lines.
5. **Run metadata** at the bottom: code SHA, prompt hash, baseline ID, eval-set hash, elapsed.

Anything beyond these five blocks is noise. The reviewer's median read time is 15 seconds.

## When evals are flaky enough to override

Sometimes the gate fails for a reason the team understands but evals can't model (a new model version released yesterday, a vendor-side outage during the run, a known-flaky judge). Allow overrides — but make them auditable:

- Override requires a **specific reason code** (`vendor-outage`, `intentional-regression`, `noise-confirmed`) recorded in the PR.
- Overrides log to the same audit stream as production decisions (see `logging`).
- A team review of overrides happens on a schedule (weekly). Recurring `noise-confirmed` overrides mean the gate threshold is wrong; recurring `intentional-regression` means the eval set is wrong. Either way the gate isn't doing its job.

The point of an override mechanism is to handle the rare case, not to be the steam valve for ongoing flakiness.

## Anti-patterns

| Anti-pattern | Why it fails |
| --- | --- |
| Gate on aggregate only | Tail regressions hide in the average. |
| Run the full suite on every commit | Cost balloons, signal drowns in CI run-time. |
| Floating rolling-average baseline | Slow regressions drift past, never tripping the gate. |
| Single-seed LLM judge | False-positive failures train the team to ignore the gate. |
| Auto-update the baseline on green | Removes the value of having a baseline. |
| CI eval results disappear after the run | Historical comparison is impossible without them. Persist forever. |
| Override mechanism with no reason code | Becomes the default path; gate is decoration. |
| Block PRs but not nightly main runs | Drift from model-side updates lands silently between PRs. |

## Behavior when invoked

1. Verify the `evals` prerequisites are in place. If not, run that skill first.
2. Pick the CI platform (GitHub Actions is the default; the pattern transfers).
3. Add path-filtered triggers for the behavior-affecting paths.
4. Wire the runner: load baseline, run suite (or stratified sample), produce per-case results with metadata.
5. Wire the gates: per-tag deltas + regression-case absolute, with calibrated noise thresholds.
6. Wire the PR comment: headline, per-tag table, top-5 case diffs, cost/latency, metadata.
7. Wire baselines: `evals/baseline.json` + `evals/baselines/` history, with an explicit "refresh-baseline" workflow that's a separate PR.
8. Add a nightly schedule against `main` for drift catching.
9. Document the override mechanism and the weekly override review.

## What this skill will NOT do

- Run the full eval suite on every commit by default.
- Gate on aggregate score alone.
- Auto-refresh the baseline. Baselines are deliberate decisions.
- Use single-seed LLM judges for gating.
- Add an override mechanism without a structured reason code.
- Skip the cost cap "because the eval set is small today."

## Templates

- `templates/run-evals.yml` — GitHub Actions workflow with path filters, label-based opt-in for draft PRs, a nightly schedule, eval-result caching, and PR-comment posting.
- `templates/eval_ci.py` — CI entrypoint: stratified sampling, N-seed judging with median, per-tag gate evaluation against a pinned baseline, structured JSON result for the comment generator.
- `templates/pr_comment.py` — renders the PR comment markdown (headline / per-tag table / top-5 case diffs / metadata) from the structured result.
