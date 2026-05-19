---
name: memory
description: Scaffold persistent memory for AI agents — categorized markdown-file store with an index, retrieval rules, and hygiene policies. Use when an agent needs to remember across sessions, not just within one conversation.
---

# memory

Sets up a file-based persistent memory system so an agent can carry context across sessions without re-asking the user the same questions. Designed to be inspectable (it's just markdown), portable (no DB dependency for the base case), and upgradable (swap to a vector store once the corpus grows past a few hundred entries).

## When to trigger

- User types `/memory`.
- User asks the agent to "remember", "save this for next time", "stop asking me X every session", or builds an agent that should learn user preferences.
- An agent project has no persistence layer between runs.

## Memory categories

Four types — different lifetimes, different retrieval triggers. Forcing categorization at write time prevents the memory store from becoming a pile of unrelated facts.

| Type | Lifetime | Holds |
| --- | --- | --- |
| `user` | Long, mostly static | Role, expertise, communication preferences, tools they use |
| `feedback` | Long | Corrections and confirmations of agent behavior; lead with the rule, then *why*, then *how to apply* |
| `project` | Medium, decays | Active initiatives, deadlines, decisions with their motivation |
| `reference` | Long | Pointers to external systems (dashboards, tracker projects, runbooks) |

## What NOT to store

These exclusions hold *even if the user explicitly asks*:

- Information derivable from the codebase, `git log`, or `git blame`.
- Ephemeral task state — that belongs in a TODO list or a plan, not memory.
- Solutions to one-off bugs — the fix lives in the code; the commit message has the context.
- Anything already in a CLAUDE.md / AGENTS.md / project README.
- Activity summaries or PR lists — keep only what was *surprising* or *non-obvious*.

When asked to save something in this list, push back and ask what was non-obvious about it — that's the part worth keeping.

## Storage layout

```
~/.agent-memory/<project-slug>/
├── MEMORY.md                 # one-line index, always loaded into context
├── user_role.md
├── feedback_testing.md
├── project_q2_migration.md
└── reference_grafana.md
```

Each memory file uses YAML frontmatter:

```markdown
---
name: short descriptive name
description: one-line hook used for relevance ranking — be specific
type: user | feedback | project | reference
---

Body. For feedback and project entries, structure as:

  Rule or fact.
  **Why:** the motivation (incident, constraint, deadline).
  **How to apply:** when this kicks in.
```

`MEMORY.md` is an index, not a memory. One line per entry, under ~150 chars:

```
- [User role](user_role.md) — senior data engineer, prefers Polars over pandas
- [Testing approach](feedback_testing.md) — integration tests must hit a real DB
```

## Retrieval rules

- Always load `MEMORY.md` at conversation start.
- Pull the full body of a memory only when its index line looks relevant to the current turn — don't preload everything.
- Before acting on a memory that names a file path, function, or flag, **verify it still exists**. Memories go stale; current code is authoritative.
- For "what's recent" or "current state" questions, prefer `git log` over snapshot-style memories.

## Write rules

- One concept per file. Splitting is cheap; merging is hard.
- Before writing, search existing memories — update an existing entry rather than creating a duplicate.
- Convert relative dates to absolute at write time (`"Thursday"` → `"2026-05-22"`), so the memory stays interpretable months later.
- For feedback memories, save on **both** corrections *and* validated successes — saving only corrections drifts the agent toward over-cautious behavior.

## Hygiene

- When a memory is contradicted by current state, **update or delete it**, don't add a second conflicting entry.
- Project memories decay — review and prune ones older than ~6 months unless the *Why:* is still load-bearing.
- Never commit `~/.agent-memory/` into a project repo. It's user-scoped, not project-scoped.

## Privacy

- Apply the same redaction rules as the `logging` skill before writing: drop secret-like keys, mask token patterns, truncate huge strings.
- If a memory would need to store a credential, store a *pointer* to where the credential lives (1Password item ID, env var name) — never the value.

## Behavior when invoked

1. Detect the agent runtime and where (if anywhere) it currently persists state.
2. Create `~/.agent-memory/<project-slug>/MEMORY.md` if missing.
3. Wire memory load/save into the agent's run lifecycle: load `MEMORY.md` on start; offer to save new memories at natural pause points.
4. Walk the user through one example entry per category so the file layout becomes concrete.

## What this skill will NOT do

- Auto-save every fact mentioned in conversation. Memory bloat is worse than memory absence — only save what passes the "non-obvious and reusable" bar.
- Persist secrets, even by accident. Redact first, write second.
- Overwrite an existing memory store without confirming and (if non-empty) backing it up.

## Templates

- `templates/memory_entry.example.md` — a sample feedback entry showing frontmatter and the *Why* / *How to apply* structure.
- `templates/memory_store.py` — a minimal Python file-based memory store with read, write, search-by-description, and index regeneration.
