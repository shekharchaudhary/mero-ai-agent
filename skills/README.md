# Skills

A curated set of scaffold skills for building reliable AI agents. Each skill is self-contained, follows the same file shape, and can be dropped into an agent harness (Claude Code or a custom SDK app) without pulling in the rest of the repo.

## Index

| Skill | Use it when |
| --- | --- |
| [security-guardrails](security-guardrails/) | Starting a new agent project or hardening an existing one — sets up tool permissions, secret protection, destructive-command guards, network egress controls, prompt-injection defenses, and audit logging. |
| [logging](logging/) | An agent has no observability beyond `print()` — scaffolds structured JSON logs, trace IDs across tool calls, PII redaction, and token/cost tracking. |
| [memory](memory/) | An agent needs to remember across sessions, not just within one conversation — sets up a categorized markdown-file store with an index, retrieval rules, and hygiene policies. |
| [evals](evals/) | Before tuning prompts or upgrading a model — scaffolds a golden dataset, deterministic and LLM-judge scorers, regression tracking, and CI integration. |
| [prompt-engineering](prompt-engineering/) | Writing or refining a system prompt — six-section skeleton, output-schema selection, Anthropic prompt-caching layout, and an iteration discipline that avoids overfitting. |
| [tool-design](tool-design/) | Adding a tool to an agent or auditing the tool surface — naming, input schemas, output envelope, idempotency, side-effect classification, and an MCP server scaffold. |
| [error-handling](error-handling/) | An agent silently fails, retries forever, or burns cost in a doom loop — classifies retryable vs terminal errors, caps retry budgets, detects no-progress, and structures escalation to the user. |
| [deployment](deployment/) | Moving an agent off your laptop — pins the model/prompt/tools tuple, externalizes secrets and state, wires per-tenant cost caps, health checks that probe the model, and canary rollout by user hash. |
| [cost-tracking](cost-tracking/) | A surprising invoice is on the horizon — a per-call ledger with tenant/run attribution, price-versioned cost math, real-time budget caps, cache-hit-rate monitoring, and cost-per-outcome reporting. |
| [rag](rag/) | An agent needs to ground answers in a corpus larger than the context window — structural chunking, hybrid retrieval with re-ranking, prompt assembly that caches well and cites sources, and retrieval evals separate from generation evals. |
| [multi-agent](multi-agent/) | One agent is hitting context limits, needs parallelism, or needs specialists — decide first if multi-agent is actually warranted, then pick the pattern (router/pipeline/orchestrator/hierarchical/debate), model subagents as typed tools with isolated context and bounded depth + fan-out. |
| [human-in-the-loop](human-in-the-loop/) | The agent takes irreversible actions or needs quality oversight evals can't catch — multi-signal escalation gates, a durable approval queue with explicit timeout policies, focused reviewer surface, and a feedback pipeline that refuses to collect signals it can't route. |
| [eval-driven-ci](eval-driven-ci/) | Evals exist but nobody runs them on every change — wires `evals` into CI with per-tag gating against a pinned baseline, N-seed judge noise control, selective runs on behavior-affecting paths, cost caps, and a PR comment that surfaces the regression in 15 seconds. |

## How the skills relate

```
                    security-guardrails
                    ┌──────┴──────┐
              (runtime gate)  (audit log)
                    │              │
   tool-design ─────┤              ├──── logging
   (safe-by-design) │              │     (dev observability)
                    │              │
              prompt-engineering ──┤
              (the agent's brain)  │
                                   │
                    evals ─────────┤
                    (the scoreboard)
                                   │
                  error-handling ──┤
                  (fail loud, escalate)
                                   │
                              memory
                              (cross-session state)

                  deployment
                  (the box it all runs in:
                   pinned tuple, caps, secrets,
                   health checks that mean something)

                  cost-tracking
                  (the ledger that ties tokens, retries,
                   caches, and caps into one bill of materials)

                  rag
                  (retrieval over a corpus — chunk, hybrid search,
                   numbered citations the model can't fake)

                  multi-agent
                  (orchestration when one agent isn't enough —
                   subagents as typed tools, bounded depth/fanout)

                  human-in-the-loop
                  (humans at the decisions worth their attention —
                   approval gates, durable queue, feedback that routes)

                  eval-driven-ci
                  (evals as a gate: per-tag deltas vs pinned baseline,
                   bisection by prompt hash, PR comment in 15s)
```

- **`security-guardrails`** and **`tool-design`** layer defense in depth: design tools so misuse is hard, then enforce permissions at runtime.
- **`logging`** captures what happened; **`evals`** measures whether it was good; **`memory`** lets the agent build on prior runs.
- **`prompt-engineering`** is opinionated about iteration — every change ships with an eval number, not a vibe.

## Skill file shape

Every skill in this repo follows the same structure:

```
skills/<name>/
├── SKILL.md                  # YAML frontmatter (name, description) + body
└── templates/                # Optional — reference implementations
    └── *.{py,md,json,jsonl}
```

`SKILL.md` body sections used consistently across the set:

- **When to trigger** — phrases and contexts that should invoke the skill.
- **What it sets up** / **Patterns** — the substantive content.
- **Behavior when invoked** — step-by-step what the skill does.
- **What this skill will NOT do** — guardrails on the skill itself.
- **Templates** — pointers to reference files.

## Using a skill with Claude Code

Copy or symlink a skill directory into your local `~/.claude/skills/`:

```bash
ln -s "$(pwd)/skills/security-guardrails" ~/.claude/skills/security-guardrails
```

Then invoke it with `/security-guardrails` (or whichever name).

## Adding a new skill

1. Pick a verb-noun name (`deployment`, `error-handling`, `cost-tracking`).
2. Create `skills/<name>/SKILL.md` with the frontmatter and the sections above.
3. Add `templates/` for any reference implementations.
4. Add a one-line row to the index table at the top of this file.

Match the depth of the existing skills — opinionated, concrete, and explicit about what the skill will *not* do.

## Status

Thirteen skills shipped. The foundation is broad now — likely next directions: incident response/on-call, caching strategy, streaming/UX, agent checkpointing. Open to direction.
