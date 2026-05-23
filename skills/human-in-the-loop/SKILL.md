---
name: human-in-the-loop
description: Scaffold human-in-the-loop for AI agents — when to escalate (and when not to), durable approval queues with timeouts, a reviewer surface that isn't just the raw trace, and a feedback pipeline that actually feeds back into evals and memory. Use when an agent takes irreversible actions, operates in a regulated domain, or has quality gaps offline evals can't catch.
---

# human-in-the-loop

Human-in-the-loop (HITL) is the most over-built and under-used pattern in agent systems. Teams either escalate everything (defeating the agent's value) or nothing (defeating their own safety net), and almost always collect feedback they never use. This skill is about putting humans at the *specific* decision points where their judgment is worth more than the model's, with infrastructure (queues, timeouts, feedback capture) that respects both reviewer time and the agent's flow. Pairs with `error-handling` (escalation introduced there as a tool — this is the depth treatment), `evals` (feedback becomes labels and golden cases), `memory` (validated corrections become feedback entries), `tool-design` (irreversible-write interlocks are HITL primitives), `logging` (link approvals to trace IDs), and `cost-tracking` (reviewer time is a real line item).

## When to trigger

- User types `/human-in-the-loop`.
- User is shipping an agent that takes irreversible actions (sends mail, charges, deletes, publishes).
- User says "we need a way for someone to approve before it does X", "how do we collect feedback", "how do I know it's actually working in prod."
- An existing system asks for human approval on every step or none — both extremes are wrong.

## Decide first: where does the human actually help?

Listing the cases worth a human's attention is more important than the queue infrastructure. A human is worth the latency and cost only when at least one of these holds:

| Trigger | Why a human helps |
| --- | --- |
| **Irreversible side effect** | Cannot be undone after the fact. The cost of "rollback by hand" exceeds the cost of a 30-second approval. |
| **Policy boundary** | The action falls outside what the agent is *authorized* to do unilaterally — regulatory, legal, contractual. |
| **Cost / blast radius threshold** | A single action that exceeds N% of a tenant's monthly budget, or affects > M downstream users. |
| **Confidence below threshold** | Multiple sampled outputs disagree, schema validation fails, or the model self-reports low confidence (use as one signal among several). |
| **Novelty** | Input is far from the distribution the agent has handled before (new tenant, new input shape, first time seeing a code path). |
| **Random quality sample** | A small percentage of *passing* cases reviewed anyway — catches drift offline evals miss. |

What is **not** on this list: "the agent might be wrong." That's true for every output, and a default of "review everything" is just delegating without delegation. Escalate when escalation changes the outcome.

## Approval modes

Three modes cover most cases. Pick per trigger, not globally:

### Pre-action approval (block)

The agent stops before the action, surfaces a structured request, and waits. The action is taken only after approval.

- **Use when:** the action is irreversible or policy-bound.
- **Tradeoff:** highest latency. Reviewer becomes part of the critical path.
- **Required:** explicit timeout policy. A human-blocked agent that waits forever is a worse failure than a wrong action.

### Post-action review (flag-and-ship)

The agent acts, the action is logged, a review is queued. A reviewer can roll back or correct within an SLA window.

- **Use when:** the action *is* reversible within a short window (drafts, tentative bookings, soft delete).
- **Tradeoff:** the action lands first. Requires real rollback paths, not just "we'll fix it later."
- **Required:** soft-delete / hold semantics so rollback is actually possible.

### Confidence sampling

A small percentage of high-confidence-passing cases get reviewed anyway. Independent of the agent's decision.

- **Use when:** baseline quality monitoring; catching drift between eval refreshes.
- **Tradeoff:** pure overhead — by design these reviews mostly say "looks fine."
- **Required:** the labels feed back into the eval set and the drift-detection process. Otherwise you are paying for a review pipeline with no consumer.

## Gating signals (and the trap of self-reported confidence)

Many systems escalate on the model's own confidence score. Self-reported confidence is *one* signal among several — not the signal. Use it in combination:

- **Schema-shape failure** — output doesn't parse or doesn't validate. Always a strong signal.
- **Output disagreement** — sample N times; if disagreement is high, escalate. Beats single self-confidence reports.
- **Action-class** — irreversible-write tools always require human approval regardless of confidence.
- **Cost / step thresholds** — a run that hit N retries or M tool calls is more likely to need review.
- **Distance from known-good** — embedding distance from your golden eval cases. Far outliers are review candidates.
- **Self-reported confidence** — useful directional signal, but easy to game. Don't gate solely on it.

Combine 2-3 signals into a gating function. Document which signals trigger which mode (block vs flag).

## The reviewer surface

The biggest waste in HITL programs is showing reviewers the raw agent trace. A reviewer is not the developer; they don't need to know every tool call. They need:

- **The user's request** (what was asked).
- **The proposed action** (what is about to happen, or what just happened).
- **Why it was flagged** (which gate, which threshold).
- **The minimum context** to decide: the cited sources, the destination of an irreversible write, the diff if it's an edit.
- **Affordances**: approve, deny, edit, defer, escalate-further. Whatever lands in this list, the reviewer's median action should take **<30 seconds**.

If a review needs more than 30s of context, the gate is wrong — either the agent shouldn't have proposed that action, or the agent should have asked an upstream clarifying question.

The full trace lives behind a "show details" link, for the rare case it's needed. Don't put it on the default screen.

## Queue mechanics

A review queue is a real piece of infrastructure. Build it boring.

- **Durable.** Approvals must survive a process crash. In-memory queues are demo-only.
- **Idempotent enqueue.** Repeated `request_approval(trace_id=...)` should not produce a second queue entry.
- **Priority.** At minimum: `irreversible` > `policy` > `confidence` > `sample`. SLAs differ per priority.
- **Deduplicate on a key** (e.g. `(tenant_id, trace_id, action_hash)`), not just `trace_id` — agents retry.
- **Per-reviewer routing.** Skills (language, domain), time zone, conflict-of-interest (a reviewer shouldn't review their own actions).
- **Per-action SLA** with explicit timeout policy: **default-allow** (low-risk, e.g. drafts), **default-deny** (high-risk, e.g. publish), or **default-defer** (escalate further). Always make this explicit per action class.
- **No unbounded growth.** Queue depth has a cap; past it, escalate or auto-decline new low-priority items.
- **Audit-logged.** Every decision (who, when, what, why) is permanent and links back to the agent's trace_id.

See `templates/review_queue.py` for the shape.

## Two-person review for the highest stakes

For irreversible actions above a value threshold (large refunds, mass emails, public posts), require **two distinct reviewers**. The two-key principle isn't bureaucratic — it's the cheapest defense against a single compromised reviewer account, a single distracted reviewer, or a single misclicked approval. Common rules:

- Different reviewers — same person can't approve both sides.
- The second reviewer sees the first's decision but not their reasoning until after deciding (avoid anchoring).
- Either denial kills the action.

This is overkill for routine reviews. Don't apply it universally — apply where blast radius justifies the friction.

## Interrupting the agent cleanly

When the agent must wait for approval, it cannot just `time.sleep` — runs may be long-lived, processes restart.

- **Serialize the run state** at the interrupt point: trace_id, scratchpad, pending action, tool history.
- **Persist** to durable storage keyed by trace_id.
- **Issue the approval request**, including the trace_id and resume token.
- **On approval/denial/timeout**, load the run state and resume from the interrupt point.
- **Resume is idempotent.** Resuming twice from the same approval must not execute the action twice. Use the approval ID as an idempotency key (see `tool-design`).

This pattern is essentially a checkpoint. Build it once; reuse for HITL, long-running multi-agent work (`multi-agent`), and crash recovery.

## Feedback that actually feeds back

A feedback collection step that has no downstream consumer is worse than no feedback — it manufactures a false sense of improvement. Every feedback signal must have a documented destination:

| Signal | Destination |
| --- | --- |
| Reviewer approved / denied | Cost ledger (`cost-tracking`), trace log (`logging`), routing-decision eval set. |
| Reviewer edited the action | Add the (input, edited output) as a golden case in `evals/dev/`. After a quarter of edits accumulate, refresh `evals/eval/` from new examples. |
| Reviewer wrote a free-form correction | Triage: validated rule-level corrections become **feedback memories** (`memory`); one-off corrections stay in the eval set as cases. |
| End-user thumbs-up / thumbs-down | Aggregate by trace_id, route negatives to a queue if they cross a per-feature rate threshold. |
| End-user free-form complaint | Inbox + weekly triage. Recurring themes become eval categories. |

The bar for adding to memory is the same as in the `memory` skill: validated, non-obvious, reusable. Most feedback is *not* a memory — it's an eval case.

## Privacy and consent

Reviewer pipelines see user data. Treat them with the same care as logs:

- **Apply the same redaction rules** as `logging` before content reaches a reviewer. PII a reviewer doesn't need to see should not be on the screen.
- **Reviewer access is audited.** Each reviewer view is a logged event linking reviewer_id, trace_id, viewed_at.
- **Consent gates feedback that flows to evals/memory.** Some content can be reviewed but not retained. Tag the feedback record with its allowed downstream uses, and respect those tags in the pipeline.

## Anti-patterns

| Anti-pattern | Why it fails |
| --- | --- |
| Review every action | Defeats the agent. Pick gates that change outcomes. |
| No timeout | Human-blocked agents stall indefinitely. |
| Reviewer sees the raw trace | Cognitive overload; reviewers miss the actual issue. |
| Feedback collected, never used | Manufactures a false sense of safety. |
| Single reviewer for high-stakes irreversible actions | Concentration risk: one mistake, no second check. |
| Self-reported confidence as the only signal | Easy to game; correlates weakly with correctness. |
| Same person who built the prompt approves the production actions | Confirmation bias; rotate reviewer pools. |
| HITL replaces evals | They complement: evals catch known regressions cheaply; HITL catches what evals can't model. |
| Approving via Slack messages without an audit log | No durability, no auditability, no SLA. |

## Behavior when invoked

1. Enumerate the actions the agent can take. Tag each with action-class (read, reversible write, irreversible write) and policy class.
2. Pick the *minimum* set of gates that covers the actions above (favor blocking on irreversible, sampling for monitoring, post-review for reversibles).
3. Scaffold the durable approval queue with priorities, dedup keys, and explicit timeout policies per action class.
4. Build the reviewer surface — focused on the action and the gate reason, not the trace.
5. Wire feedback collection with explicit downstream destinations. No collection without a consumer.
6. Add metrics: queue depth, p50/p95 review latency, approval rate per gate, post-review rollback rate, reviewer cost per action.
7. Document the two-person rule for the action classes that warrant it.

## What this skill will NOT do

- Wrap every agent action in approval. The agent then has the value of an autocomplete with extra steps.
- Surface raw transcripts to reviewers as the default UI.
- Build a feedback pipeline without a named consumer for each signal.
- Use self-reported confidence as a sole gate.
- Allow a human-blocked run to hang without a timeout and a default policy.
- Treat HITL as a substitute for `evals`. Both are required.

## Templates

- `templates/approval_gate.py` — gating function combining schema-shape, action-class, output-disagreement, cost-threshold, and confidence signals into a single `should_escalate()` decision.
- `templates/review_queue.py` — durable JSONL-backed queue with priority, idempotent enqueue, per-action-class timeout policies (default-allow / default-deny / default-defer), and audit-logged decisions.
- `templates/feedback_collector.py` — structured feedback capture with explicit downstream tags (eval, memory, ignore), redaction at write-time, and a pipeline hook that routes each signal to its consumer.
