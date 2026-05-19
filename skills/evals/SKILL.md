---
name: evals
description: Scaffold an evaluation suite for AI agents — golden datasets, deterministic and LLM-judge scorers, regression tracking across model versions, and CI integration. Use before tuning prompts or upgrading models, not after.
---

# evals

Sets up a measurable feedback loop for agent quality. Without evals you cannot tell whether a prompt tweak, model upgrade, or new tool actually helped — you can only tell whether the example you tried still works. This skill establishes the dataset, the scorers, and the harness so every change ships with a number, not a vibe.

## When to trigger

- User types `/evals`.
- User is about to upgrade a model version, swap a prompt, or rewrite a tool.
- User says "it's better now" / "this is worse" / "did that regress anything" — those claims need numbers.
- An agent project has no test suite beyond `if it runs, ship it`.

## The four eval families

Pick the simplest family that captures the property you care about. Don't reach for an LLM judge when a regex will do.

| Family | Use when | Cost |
| --- | --- | --- |
| **Deterministic** | Output must match exactly, satisfy a regex, parse as JSON, or contain/exclude a string. | Free |
| **Schema / format** | Output must conform to a schema (Pydantic, zod, JSON Schema). | Free |
| **LLM-as-judge** | Quality is subjective (helpfulness, tone, faithfulness to source). | $$, slow, noisy |
| **Pairwise (A/B)** | Comparing two versions where absolute quality is hard to score. | $$, but more reliable than absolute judges |

Prefer deterministic + schema for ≥70% of your cases. Reserve LLM-judges for the cases nothing else fits.

## Dataset structure

One JSONL file per eval set. Each line:

```json
{
  "id": "stable-unique-id",
  "input": "...",
  "expected": "...",
  "tags": ["happy-path", "edge-case", "adversarial", "regression"],
  "provenance": "user-report#123 | manual | synthetic | bug-fix"
}
```

- **`id`**: stable. Never reuse. New cases get new IDs.
- **`provenance`**: where the case came from. Cases derived from real bugs/complaints are higher-signal than synthetic ones — track which is which.
- **`tags`**: at minimum tag the case type. Failures on `adversarial` matter differently than failures on `happy-path`.

Coverage targets:

- ~50% happy paths.
- ~30% edge cases (empty inputs, very long inputs, unusual unicode, ambiguity).
- ~15% adversarial (prompt injection attempts, jailbreaks, off-topic requests).
- ~5% regression cases (one per fixed bug — never delete these).

## The golden rule

**Never tune prompts against the eval set.** The moment you iterate on a prompt while watching the eval score, the eval set becomes training data and stops predicting real-world quality. Maintain two disjoint sets:

- **`dev/`** — the set you iterate on. Inspect freely, change prompts in response to failures.
- **`eval/`** — the held-out set. Look at the score, not the individual examples. Treat it like a test set.

When eval scores stop tracking real-world quality (users complain about things evals pass), refresh the eval set from new real-world traces, don't keep tuning to the old one.

## Scoring patterns

### Deterministic

```python
def score_exact(output: str, expected: str) -> float:
    return 1.0 if output.strip() == expected.strip() else 0.0
```

### Schema

```python
def score_valid_json(output: str) -> float:
    try:
        json.loads(output)
        return 1.0
    except json.JSONDecodeError:
        return 0.0
```

### LLM judge

- Use a **stronger** model than the one being evaluated. Same-model judging inflates scores.
- Prompt the judge for a **rubric score with reasoning**, not just a number. Inspect the reasoning when debugging.
- Run each case 3x and take the mean — judges are noisy.
- Calibrate: hand-label ~50 cases and check judge agreement. If agreement <80%, the rubric is wrong, not the model.

### Pairwise

- Randomize order (A/B vs B/A) per case to control for position bias.
- Report win-rate with confidence intervals, not just "A wins 32/50."

## What to measure

Beyond correctness:

- **Latency**: p50, p95, p99 per case.
- **Cost**: tokens × price, summed per run.
- **Tool-use accuracy**: did it call the right tool with the right args (compare against expected tool trace).
- **Refusal rate**: per category. Spike in refusals on benign inputs is a regression even if accuracy is unchanged.
- **Failure modes**: cluster failures by tag and by error type — don't just look at the aggregate score.

## Regression tracking

- Store every run's per-case scores, not just the aggregate. You need per-case diffs to find what broke.
- Stamp each run with: model version, prompt hash, code SHA, eval set version, date.
- Keep historical runs forever — they're cheap and they're the only way to attribute regressions to a specific change.
- In CI: run evals on every PR that touches prompts, tools, or the model config. Fail the build on regression beyond a noise threshold (e.g. >2pp drop on aggregate or any drop on `regression` tag).

## Cost controls

LLM-judge evals can quietly run up bills. Always:

- Cap dollars per run. Abort and report partial results if exceeded.
- Sample large eval sets in dev; run the full set only in CI.
- Cache scorer outputs by `(case_id, output_hash, scorer_version)` — re-running the same eval shouldn't re-pay for judging.

## Behavior when invoked

1. Detect the agent runtime and whether evals already exist.
2. Generate `evals/dev/`, `evals/eval/`, and `evals/results/` directories with a `.gitkeep` and a starter dataset.
3. Wire up a runner that supports all four scorer families.
4. Walk the user through writing the first ~10 cases — half happy-path, half edge/adversarial — drawn from real prompts they've already seen the agent handle (or mishandle).
5. Add a CI step (GitHub Actions) that runs `eval/` on PRs and posts the score delta.

## What this skill will NOT do

- Generate synthetic eval cases as the entire dataset. Synthetic data is fine as filler, but the first 20 cases must come from real usage — synthetic-only sets test the data generator, not the agent.
- Use the same model as both subject and judge by default.
- Auto-update the eval set when a case starts failing. A failure is a finding, not a bug in the eval — investigate before changing the expected output.
- Report a single aggregate score without the per-tag breakdown.

## Templates

- `templates/evalset.example.jsonl` — starter cases spanning happy/edge/adversarial/regression tags.
- `templates/judge_rubric.example.md` — sample LLM-judge rubric with a scoring scale and what to ignore.
- `templates/run_evals.py` — runner with deterministic, schema, and judge scorers; JSON results output; per-tag breakdown.
