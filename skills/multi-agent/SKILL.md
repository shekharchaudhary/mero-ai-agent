---
name: multi-agent
description: Scaffold multi-agent orchestration — when it's worth the cost, which pattern fits, how to bound subagent context/budget/depth, and how to aggregate results without leaking the whole sub-conversation back to the parent. Use when a single agent is hitting context limits, needs parallelism, or needs specialist behavior the system prompt can't capture.
---

# multi-agent

A multi-agent system is a coordination problem dressed up as an architecture. It can buy you parallelism, specialization, and context isolation — and it can also multiply your latency, cost, and failure surface 5×. This skill is about deciding when multi-agent is the answer, picking the pattern that fits, and bounding the coordination so it doesn't become its own bug factory. Pairs with `error-handling` (per-subagent budgets and no-progress detection), `cost-tracking` (multi-agent multiplies tokens — attribute every subagent), `logging` (parent/child trace IDs are the only way to debug what happened), and `tool-design` (subagents are best modeled as tools, not as conversational peers).

## When to trigger

- User types `/multi-agent`.
- User has a single agent that exceeds context limits, runs too slow, or struggles to combine multiple specialties.
- User says "I want one agent for X and another for Y" / "have agents talk to each other" / "use a planner + worker pattern."
- An existing system has agents that spawn agents that spawn agents with no depth bound.

## First: do you actually need multi-agent?

Most tasks don't. Before adding orchestration, exhaust the cheaper options:

| Symptom | Cheaper fix |
| --- | --- |
| Context is too long | Better retrieval (`rag`), prompt-caching layout (`prompt-engineering`), or summarization sub-step |
| Agent confuses unrelated tasks | Sharper system prompt with section headers, narrower tools |
| Wants "expert" behavior | One agent with a richer system prompt + better examples |
| Needs to do N things in parallel | A single agent with parallel tool calls — same model, same context, just concurrent |
| Workflow has clear stages | A pipeline of functions calling the model with different prompts — not a "team of agents" |

Multi-agent earns its complexity when: the work *truly* parallelizes (independent subproblems), specialists need *different* tool sets, or context isolation is a feature (one agent should not see another's reasoning).

## Pick the pattern

Five patterns cover ~95% of real systems. Pick the simplest that fits.

### 1. Router

One agent inspects the input and dispatches to one of N specialists. The router doesn't do the work — it picks the worker.

- **Use when:** inputs are heterogeneous and specialists need distinct prompts/tools. Customer-support triage, bug-vs-feature classification, language-specific code review.
- **Avoid when:** a single prompt with conditional instructions would do.
- **Failure mode:** router misroutes silently. Mitigation: log the routing decision and the reasoning; eval the router separately.

### 2. Pipeline (sequential)

Specialists hand off in sequence: extract → enrich → validate → format. Each stage transforms the artifact.

- **Use when:** stages have clear inputs/outputs and each stage is non-trivial.
- **Avoid when:** stages are tiny and you're just chaining model calls for the sake of it.
- **Failure mode:** an early-stage error compounds downstream. Mitigation: validate the artifact between stages; abort early on invalid output.

### 3. Orchestrator + workers (fan-out / fan-in)

A coordinator decomposes the task, dispatches independent subtasks to workers in parallel, then aggregates. This is the most useful pattern when work parallelizes.

- **Use when:** subtasks are independent and the wall-clock win from parallelism outweighs the coordination cost.
- **Avoid when:** subtasks have real dependencies (use pipeline).
- **Failure mode:** one slow worker blocks the whole batch (head-of-line). Mitigation: per-worker timeout, partial-result aggregation, graceful degradation.

### 4. Hierarchical

A top-level coordinator delegates to mid-level coordinators which delegate to workers. Same shape as orchestrator+workers, recursive.

- **Use when:** the problem genuinely decomposes hierarchically (research → topic → subtopic). Rare.
- **Avoid when:** the second level is mostly forwarding work — collapse it. Each layer adds latency and cost.
- **Failure mode:** unbounded recursion. Mitigation: **enforce a hard depth limit at the orchestrator** (default 2-3). No exceptions.

### 5. Debate / critique

Two agents (or more) review each other's output. Generator proposes; critic checks; arbiter resolves.

- **Use when:** quality is more important than latency, and the critic genuinely catches what the generator misses (verify with evals).
- **Avoid when:** the critic is the same model with the same prompt — it'll mostly agree with itself. Use a different model, different prompt, or both.
- **Failure mode:** runaway back-and-forth. Mitigation: hard round cap (default ≤ 2 rounds of critique).

## Subagents should be tools, not chat partners

The temptation in multi-agent design is to model agents as conversational peers — they message each other, see each other's reasoning, build up a shared "team transcript." This pattern looks elegant on a whiteboard and produces hard-to-debug systems in practice.

A more robust default: **expose each subagent as a typed tool** to whoever calls it.

- **Inputs are a typed schema.** The caller passes structured arguments, not free-form messages.
- **Outputs are a typed envelope.** A summary plus optional structured fields (see `tool-design`).
- **Each subagent has its own isolated context.** It does not see the parent's full conversation — only what the schema passes in.
- **The subagent returns once, then dies.** No long-lived "agent personas" — the next call is a fresh subagent.

This gives you tool semantics (validation, idempotency, retries, timeouts, observability) for free. Conversational multi-agent gives you none of those.

## Context discipline

Multi-agent systems blow up most often in one of two ways: the parent's context window fills with raw subagent transcripts, or a subagent inherits everything the parent has seen.

Rules:

- **Subagents see only what they need.** Construct a focused prompt from the typed inputs. Do *not* pass the parent's conversation.
- **Subagent outputs are summarized before they return.** The parent gets the structured envelope, not the subagent's full transcript. If the parent needs the raw work, store it under a handle (an id) the parent can fetch on demand.
- **No shared scratchpad without a schema.** "Just write what you found to a shared notes file" creates an unbounded blob that every subsequent agent must re-read.

## Budget propagation

A run has a global budget (wall-clock, cost, tokens). Subagents must inherit a slice of it, not start fresh.

- **Subagent budget < parent's remaining.** Compute on dispatch: `child_budget = min(default_child_budget, parent.remaining - reserve)`.
- **Reserve for aggregation.** Aggregating N subagent results itself costs tokens. Reserve 10-20% of the parent budget for the final aggregation call.
- **Reject dispatch when over budget.** A subagent dispatched after the global budget is exhausted is a bug, not a graceful retry. Return a `budget_exhausted` outcome and stop.

## Depth and fan-out caps

Two hard limits prevent unbounded growth:

- **Depth cap.** Default ≤ 3 levels of nesting. A subagent that tries to spawn a sub-subagent at depth 3 gets a hard error, not a silent extra layer.
- **Fan-out cap.** Default ≤ 10 parallel subagents. Past that, the aggregation step becomes its own context-management problem; usually a sign the decomposition is too fine.

Both caps belong in code (an `OrchestratorConfig`), not in a prompt. Models will exceed limits stated in prompts.

## Parallelism

If subtasks are independent, dispatch them in parallel — sequential dispatch wastes the main advantage of orchestrator+workers.

- **Use the model's parallel-tool-call feature if available.** Single agent, multiple concurrent tool invocations beats spawning separate agents for trivial parallelism.
- **For real subagent fan-out**, use the language's concurrency primitive (`asyncio.gather`, `Promise.all`, goroutines).
- **Set per-worker timeouts** — head-of-line blocking from one slow worker is the most common multi-agent slowness.
- **Accept partial results** when feasible. Aggregate over the workers that returned in time; mark the rest as `timeout` in the outcome.

## Failure handling across agents

What does it mean for a multi-agent run to "fail"? Decide explicitly per pattern:

| Pattern | Failure policy |
| --- | --- |
| Router | If specialist fails, surface to user — don't try a different specialist as a fallback (it's gaslighting the input). |
| Pipeline | Fail-fast: an invalid intermediate artifact aborts the rest. Re-running starts from the failed stage. |
| Orchestrator + workers | Tolerate partial failure (return aggregated results + list of failed subtasks) when possible. Fail the run only if a critical subtask failed. |
| Hierarchical | Same as orchestrator+workers at each level, bubble up. |
| Debate | If the critic times out, return the generator's output with a "not reviewed" flag, not a missing response. |

In every case: every subagent invocation should produce a typed outcome (`success`, `failure`, `timeout`, `budget_exhausted`) the orchestrator can switch on.

## Observability

The single most useful thing in a multi-agent system is the trace.

- **One trace_id per top-level run.** Propagate it into every subagent call.
- **Parent / span / child relationships.** Each subagent call gets a `span_id` with `parent_span_id` pointing at the dispatcher.
- **Tag every log line** with `agent_role` (`orchestrator`, `worker`, `router`, `critic`) so you can filter.
- **Track aggregate cost per role.** Often the orchestrator's aggregation step is the single most expensive call — easy to miss without per-role attribution.

See `logging` for the structured-log shape.

## Common failure modes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Total cost is 10× expected | Subagents inherit the parent's full transcript | Pass typed inputs only |
| One slow run blocks everything | Sequential dispatch where parallel would do | Use `asyncio.gather` etc. with per-worker timeout |
| Orchestrator context overflows | Raw subagent transcripts returned to parent | Summarize before return; store raw under a handle |
| Subagents spawn endlessly | No depth cap | Hard depth limit in code, not in prompt |
| Critic always agrees with generator | Same model + same prompt | Different model or rubric |
| "Sometimes it works" | Race conditions in shared state | No shared mutable state without a schema |
| Router picks wrong specialist often | Router not eval'd separately | Build a router-only eval set; iterate the routing prompt alone |

## Behavior when invoked

1. Confirm the user has tried the cheaper single-agent fixes from the table above.
2. Pick a pattern from the five. Force a choice — "agents collaborate" is not a pattern.
3. For each subagent: define its typed input schema, typed output envelope, isolated system prompt, tool set, and budget slice.
4. Wire orchestration with parallelism where independent and timeouts everywhere.
5. Enforce depth and fan-out caps in the orchestrator config.
6. Add per-role logging tags and parent/child trace propagation.
7. Add a separate eval surface for the routing/aggregation logic — those have to be measured independently of the workers.

## What this skill will NOT do

- Build a multi-agent system when a single agent + better prompt would solve it.
- Model subagents as long-lived conversational peers.
- Pass the parent's full transcript to subagents.
- Return raw subagent transcripts to the parent context.
- Allow unbounded depth or fan-out.
- Skip per-subagent timeouts — head-of-line blocking is too common.
- Treat the critic in a debate pattern as automatically improving quality. Validate it earns its keep with evals.

## Templates

- `templates/orchestrator.py` — orchestrator+workers with parallel async dispatch, per-worker timeout, budget propagation, depth and fan-out caps, partial-result aggregation, and parent/child trace tagging.
- `templates/subagent_as_tool.py` — shows how to expose a subagent as a typed tool from the parent's perspective: typed input schema, typed envelope, isolated prompt, summarized output.
