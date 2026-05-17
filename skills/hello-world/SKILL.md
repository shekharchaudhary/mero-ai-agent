---
name: hello-world
description: A minimal sample skill that greets the user. Use as a template when scaffolding new skills in this repo.
---

# hello-world

A minimal example skill. Demonstrates the structure every skill in this repo should follow:

1. A `SKILL.md` with YAML frontmatter (`name`, `description`) followed by the prompt body.
2. Optional supporting files (scripts, prompts, configs) alongside `SKILL.md`.

## When to trigger

Invoke when the user types `/hello-world` or asks for a greeting demo.

## Behavior

When invoked:

1. Greet the user by name if known, otherwise say "Hello, world!".
2. Print the current date.
3. Offer one short tip about using skills in Claude Code.

Keep the response under three lines.

## Example

```
User: /hello-world
Assistant: Hello, world! Today is 2026-05-17.
Tip: skills live in ~/.claude/skills/ and are invoked with /<name>.
```
