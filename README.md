# mero-ai-agent

A workspace for building, testing, and curating skills for my AI agents.

## Overview

This repository is a personal lab for developing reusable agent skills — small, composable capabilities (slash commands, hooks, prompt templates, tool wrappers) that extend AI coding agents such as Claude Code.

Each skill is self-contained so it can be dropped into an agent harness without pulling in the rest of the repo.

## Repository layout

```
mero-ai-agent/
├── skills/        # Individual skill packages (one folder per skill)
├── scripts/       # Helper scripts for local dev and testing
└── README.md
```

> The `skills/` and `scripts/` folders will be added as work lands.

## Getting started

Clone the repo:

```bash
git clone https://github.com/shekharchaudhary/mero-ai-agent.git
cd mero-ai-agent
```

To use a skill with Claude Code, copy or symlink the skill folder into your `~/.claude/skills/` directory, then invoke it with `/<skill-name>`.

## Adding a new skill

1. Create a new directory under `skills/<skill-name>/`.
2. Add a `SKILL.md` describing what the skill does and when to trigger it.
3. Include any supporting files (prompts, scripts, configs) the skill needs.
4. Test the skill end-to-end before committing.

## Status

Early-stage and evolving. Expect breaking changes.

## License

Personal project — no license granted yet. Reach out before reusing.
