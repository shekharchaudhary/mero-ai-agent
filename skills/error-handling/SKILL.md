---
name: error-handling
description: Scaffold error handling, retries, timeouts, and escalation for AI agents — classify what's retryable, cap retry budgets, detect no-progress loops, and know when to ask the user. Use when an agent silently fails, retries forever, or burns cost in a doom loop.
---

# error-handling

Agents fail in a few specific shapes — transient API errors, malformed outputs, tools that error, and silent doom loops where the agent makes the same wrong call ten times in a row. This skill codifies what to retry, how, with what budget, and when to stop and escalate. Pairs with `tool-design` (which defines what a "retryable error" *means* at the tool boundary) and `logging` (which records every retry so you can find the doom loops after the fact).

## When to trigger

- User types `/error-handling`.
- User says "it keeps retrying" / "it gives up too fast" / "it just hangs" / "it lost $X to a loop."
- An agent project uses bare `try / except: pass` or `time.sleep(n); retry()` in tight loops.
- Adding any new external dependency (model API, tool API, DB) — that's a new failure mode to plan for.

## Classify errors before you handle them

Different errors need different responses. The single biggest error-handling bug is treating them uniformly. Tag every error at the boundary it crosses:

| Class | Examples | Response |
| --- | --- | --- |
| **Transient** | rate limit, 503, timeout, network blip | Retry with backoff + jitter |
| **Overloaded** | model overloaded, capacity error | Retry with longer backoff, optionally fall back to a smaller model |
| **Invalid input** | bad schema, malformed args, validation failure | Do **not** retry as-is. Mutate or escalate. |
| **Permission** | 401, 403, not authorized | Do not retry. Escalate. |
| **Not found** | 404, missing resource | Do not retry. Try a different identifier or escalate. |
| **Conflict** | 409, version mismatch, idempotency collision | Refresh state, then retry once |
| **Context-length** | prompt too long | Truncate / compact, then retry once |
| **Internal** | unhandled exception in your code | Don't swallow. Surface and crash the run. |

Use the typed error codes from `tool-design` (`not_found`, `permission_denied`, `rate_limited`, etc.) so this classification can be table-driven, not per-call ad-hoc.

## Retry mechanics

When retrying transient errors, three things matter:

1. **Exponential backoff with jitter.** Pure exponential causes thundering herd when many agents retry in sync. Full-jitter: `delay = random_between(0, base * 2 ** attempt)`. Cap at a max (e.g. 30s).
2. **Bounded attempts.** Hard cap on retry count (default 3-5 for API calls, 1-2 for tool calls). Past that, escalate.
3. **Budget caps in addition to attempt caps.** A run has a wall-clock budget (e.g. 60s) and a cost budget (e.g. $1). Retries count against both. When either is exhausted, stop — even if attempts remain.

Never retry without a budget. "Retry until it works" is how agents leak $400 in an hour.

See `templates/retry_with_budget.py` for the shape.

## Timeouts at multiple layers

One timeout is not enough. An agent needs timeouts at three levels:

- **Per request** — model API call: ~30-60s. Tool call: depends on the tool, but always set one.
- **Per turn** — model call + any tool calls it spawned: a couple of minutes for most agents.
- **Per run** — the entire user task: caps a runaway agent. Default to something like 5-10 minutes.

When a timeout fires:

- Per-request: classify as transient, retry within budget.
- Per-turn or per-run: stop the agent, surface the partial state, escalate to user. Do not silently restart.

Never use `requests.get(url)` without `timeout=`. The default is "wait forever."

## Detect no-progress loops

The most expensive agent failure is not an error — it's an agent that keeps trying with no progress. Symptoms:

- Same tool called with same args repeatedly.
- Same error returned, same retry, no change of strategy.
- Turn count climbing with no state change.

Detection (cheap, run every turn):

- **Repeat-call detector**: hash `(tool_name, normalized_args)` of the last N tool calls. If the same hash appears 3+ times, the agent is looping.
- **No-state-change detector**: if the agent's internal scratchpad / files / decisions haven't changed in M turns, it's stuck.
- **Turn cap**: a hard limit (e.g. 30 turns) regardless of progress signals.

When a loop is detected: **break out of the agent loop, surface to the user with the recent trace.** Do not silently keep going.

See `templates/agent_loop_with_escalation.py`.

## Escalation: when to ask the user

The default failure mode of an agent is to keep trying. The better default for many failures is to ask. Escalate to the user when:

- An error is non-retryable (permission, not-found, invalid-input from human-supplied data).
- A retry budget is exhausted.
- A no-progress loop is detected.
- The next step would have an irreversible side effect and the agent's confidence is low.
- The agent has made the same kind of decision N times this run (charge by the dozen — confirm before continuing).

Escalation isn't a failure mode. It's a tool. A short, structured "I tried X, got Y, here's what I'd do next — proceed?" is almost always better than a confidently wrong action.

## Validation errors (output doesn't parse)

When a model's output fails to parse:

1. **Retry once** with a focused correction prompt: "Your previous response could not be parsed as JSON. Return only valid JSON matching this schema: …"
2. If the second attempt fails, switch strategy — use tool-use / structured outputs instead of free-form, or fall back to a more reliable model.
3. **Do not retry indefinitely.** If two attempts fail, the prompt is wrong, not the model.

Log both attempts. A common cause is that the model produced valid content but with surrounding prose your parser rejected — fix the prompt to forbid the prose, don't paper over with a retry loop.

## Anti-patterns

- **Bare `except: pass`.** Hides bugs forever. If you genuinely don't care about an error, log it with the type and continue.
- **Retry without classification.** Retrying a 404 wastes budget. Retrying a 401 also wastes it.
- **Retry without backoff.** Tight loops against rate-limited APIs make the rate limit *worse*.
- **Retry without idempotency.** Resending a `POST /charge` thrice charges the customer thrice. Use idempotency keys (see `tool-design`).
- **Silent fallback to a different model/path.** If you fell back, log it and surface it. Quiet fallbacks hide regressions.
- **One global `try/except` around the whole agent.** Loses all the context about *what* failed. Catch narrowly, near the operation that can fail.
- **`time.sleep(60); try_again()` in production code.** Use a real backoff with budget.

## Behavior when invoked

1. Audit the existing error-handling surface: grep for `except`, `retry`, `sleep`, `timeout`. Flag the anti-patterns above.
2. Verify every external call has a timeout. List the ones that don't.
3. Verify the agent loop has a turn cap and a no-progress detector. Add them if missing.
4. Verify retry call sites use a budget-aware retry helper, not ad-hoc `for i in range(5)`.
5. Verify validation/parse failures bail after ≤2 attempts.
6. Verify there is an escalation path — the agent has a way to surface "I'm stuck" to the user without crashing or looping.

## What this skill will NOT do

- Wrap everything in `try/except`. Errors at the wrong layer become impossible to debug.
- Add retries to non-idempotent operations without an idempotency key.
- Suppress errors that should crash the run. An unhandled exception in the agent's own code is a bug, not a transient.
- Use a one-size-fits-all retry config across read-only and side-effectful tools.
- Replace escalation with cleverer retries. Some failures need a human, full stop.

## Templates

- `templates/retry_with_budget.py` — retry helper with full-jitter backoff, attempt + wall-clock + cost caps, and a retryable-error classifier hook.
- `templates/agent_loop_with_escalation.py` — bounded agent loop with turn cap, repeat-call detector, no-progress detector, and a structured escalation message.
