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
                              memory
                              (cross-session state)
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

Six skills shipped. Likely next: error-handling/retries, deployment, cost tracking. Open to direction.
