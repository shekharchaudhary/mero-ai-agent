# mero-ai-agent

A workspace for building, testing, and curating skills for my AI agents.

## Overview

This repository is a personal lab for developing reusable agent skills — small, composable capabilities (slash commands, hooks, prompt templates, tool wrappers) that extend AI coding agents such as Claude Code.

Each skill is self-contained so it can be dropped into an agent harness without pulling in the rest of the repo.

## Skills

Twelve scaffold skills, one per folder under `skills/`. See [`skills/README.md`](skills/README.md) for the full index and how they relate.

| Skill | One-line purpose |
| --- | --- |
| [security-guardrails](skills/security-guardrails/) | Tool permissions, secret protection, destructive-command guards, prompt-injection defenses, audit logging. |
| [logging](skills/logging/) | Structured JSON logs with trace IDs, PII redaction, token/cost tracking. |
| [memory](skills/memory/) | Categorized markdown memory store with retrieval and hygiene rules. |
| [evals](skills/evals/) | Golden datasets, deterministic and judge scorers, regression tracking. |
| [prompt-engineering](skills/prompt-engineering/) | System prompt skeleton, prompt-caching layout, iteration discipline. |
| [tool-design](skills/tool-design/) | Tool naming, schemas, side-effect classification, MCP scaffold. |
| [error-handling](skills/error-handling/) | Retry budgets, error classification, no-progress detection, structured escalation. |
| [deployment](skills/deployment/) | Pinned deployment tuple, external secrets/state, cost caps, model-probing health checks, canary by hash. |
| [cost-tracking](skills/cost-tracking/) | Per-call ledger with attribution, price-versioned cost math, real-time caps, cache-hit-rate, cost-per-outcome. |
| [rag](skills/rag/) | Structural chunking with metadata, hybrid retrieval + re-ranking, numbered chunks with citation validation. |
| [multi-agent](skills/multi-agent/) | Five orchestration patterns, subagents-as-tools with isolated context, depth/fan-out caps, parallel dispatch with budget propagation. |
| [human-in-the-loop](skills/human-in-the-loop/) | Multi-signal escalation gates, durable approval queue with timeout policies, focused reviewer surface, feedback pipeline with named consumers. |

## Repository layout

```
mero-ai-agent/
├── skills/
│   ├── README.md          # index + how skills relate
│   ├── security-guardrails/
│   ├── logging/
│   ├── memory/
│   ├── evals/
│   ├── prompt-engineering/
│   ├── tool-design/
│   ├── error-handling/
│   ├── deployment/
│   ├── cost-tracking/
│   ├── rag/
│   ├── multi-agent/
│   └── human-in-the-loop/
└── README.md
```

## Getting started

Clone the repo:

```bash
git clone https://github.com/shekharchaudhary/mero-ai-agent.git
cd mero-ai-agent
```

To use a skill with Claude Code, symlink the skill folder into `~/.claude/skills/`:

```bash
ln -s "$(pwd)/skills/security-guardrails" ~/.claude/skills/security-guardrails
```

Then invoke it with `/security-guardrails`.

## Adding a new skill

See [`skills/README.md`](skills/README.md) for the file shape and conventions. In short: one folder, a `SKILL.md` with YAML frontmatter, optional `templates/`, and a row added to the index.

## Status

Twelve skills shipped. Foundational set is broad now. Next directions could be eval-driven CI, on-call/incident response, caching strategy, or streaming/UX.

## License

Personal project — no license granted yet. Reach out before reusing.
