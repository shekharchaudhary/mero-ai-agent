---
name: tool-design
description: Patterns for defining agent tools — naming, input schemas, output shape, idempotency, blast radius, and error handling. Use when adding a new tool to an agent or auditing an existing tool surface.
---

# tool-design

A well-designed tool is the difference between an agent that helps and an agent that destroys data. This skill codifies the patterns that make tools easy for the model to invoke correctly and hard for it to misuse. Pairs with `security-guardrails` (which enforces permissions at runtime) — that skill stops bad calls; this one designs tools so bad calls are harder to make in the first place.

## When to trigger

- User types `/tool-design`.
- User is adding a new tool, building an MCP server, or wrapping a CLI/SDK for agent use.
- User says "the agent keeps calling the wrong tool" / "it passes the wrong arguments" / "it doesn't know when to use X."
- Reviewing an existing tool surface that grew organically and feels sprawling.

## Five properties of a good tool

1. **Narrow.** Does one thing. If the description needs the word "and", split it.
2. **Named for the verb.** `search_issues`, not `issues`. `create_branch`, not `branch_helper`. The model picks tools by name first, description second.
3. **Strongly typed.** Every input has a schema. Required fields are required; optional fields have explicit defaults.
4. **Idempotent where possible.** Calling twice should be safe. When it can't be (e.g. sending email), document it loudly and require an explicit confirm parameter.
5. **Predictable output shape.** Same shape on success, same shape on error. The model can't reliably handle outputs that change structure based on content.

## Names and descriptions

Models pick tools the way humans pick a function from autocomplete: glance at the name, glance at the first line of the docstring, decide. Optimize for that scan.

- **Name**: imperative verb + noun. `get_user`, `update_status`, `list_pull_requests`. Avoid generic stems (`handle_*`, `process_*`, `do_*`).
- **First sentence of the description**: what the tool does, in one line, written for the model.
- **Second sentence**: when *not* to use it — the closest neighbor and why this one differs.
- **No marketing.** "Powerful, flexible, all-in-one" tells the model nothing.

Example:

```
get_user_by_email(email: str) -> User
  Look up a single user by their exact email address.
  Use list_users for partial matches or filtering by other fields.
```

If two tools' descriptions can be swapped without changing meaning, merge them or rename them.

## Input schemas

Schemas are the model's safety rails. Every parameter:

- **Has a type.** No `Any` / `object` unless genuinely unstructured.
- **Has a one-line description.** What it is, plus units, format, or constraints if non-obvious (`timeout_seconds`, not `timeout`).
- **Uses enums for closed sets.** `status: "open" | "closed"` not `status: str`. The model is dramatically more reliable with enums.
- **Has a default if optional.** Make the default the safe choice (read-only over write, dry-run over commit).
- **Avoids overloaded fields.** A field that accepts "either a string or an array of strings" causes ~10% of all tool-call errors. Pick one.

Validate at the boundary: reject malformed inputs with a clear error before any side effect runs.

## Output shape

The output is part of the API the model uses to reason about the next step. Design it intentionally.

- **Same envelope every time.** Pick a shape — `{"ok": true, "data": ...}` / `{"ok": false, "error": ...}` is fine — and keep it.
- **Summarize, then detail.** First field is a one-line summary the model will read first. Long results go in a separate field the model can choose to inspect.
- **Truncate large payloads.** Never return more than ~5 KB by default. Provide a `next_page` token or an `id` the model can pass to a follow-up tool.
- **Don't return raw stack traces.** Convert errors to typed codes (`not_found`, `permission_denied`, `rate_limited`) the model can switch on.
- **Return what changed, not the whole world.** `update_user` should return the diff, not the entire user record.

## Side effects and idempotency

Side effects are where agents do real damage. Classify each tool:

| Class | Example | Design rule |
| --- | --- | --- |
| Read-only | `get_*`, `list_*`, `search_*` | No special handling. Cache aggressively. |
| Reversible write | `create_draft`, `add_label`, `comment` | Allow, but log every call. |
| Irreversible write | `send_email`, `delete_*`, `publish_*`, `charge_*` | Require an explicit `confirm: true` parameter. Make `dry_run: true` the default. Surface clearly in the description. |

Idempotency:

- For reversible writes: use a client-supplied `idempotency_key` so retries don't duplicate.
- For irreversible writes: refuse retries without the same idempotency key. Better to fail loud than send the email twice.

## Errors

Errors are signals, not failures. The model uses them to recover.

- **Typed codes.** A short, stable string the model can branch on: `not_found`, `invalid_argument`, `permission_denied`, `rate_limited`, `conflict`, `internal_error`.
- **One sentence of context.** What the model needs to know to retry or change strategy. Not a stack trace.
- **Suggest the next step when obvious.** `"User not found. Try list_users with a partial name."` — agents follow these hints reliably.
- **Distinguish retryable from terminal.** Add a `retryable: bool` field. The model retries on `rate_limited`, gives up on `not_found`.

## Composability

- **Many small tools > one giant tool.** A `Bash` tool that does everything is hard to scope. Six narrow tools (`read_file`, `list_dir`, `grep`, `git_status`, `git_diff`, `run_tests`) are easier to permission and audit.
- **But not too many.** Past ~20 tools, the model starts misrouting. Group by domain; consider hierarchical tool sets (a top-level tool that loads a sub-tool list) when you go beyond that.
- **No hidden tool chaining.** If tool A internally calls tool B, the model can't reason about the cost or side effect of B. Expose B directly.

## Authentication and authorization

- **The tool runs as the user, not the agent.** Pass user context (token, scoped credential) into the tool; never let the tool decide who it acts as.
- **Scope every credential.** A tool that only reads should not hold a write token.
- **Authorize at the tool boundary**, not deep inside helper functions. Easier to audit; harder to bypass.
- **Never accept credentials as a tool argument** the model fills in. Inject from the trusted context.

## MCP-specific notes

When exposing tools via MCP:

- One server = one cohesive capability surface. Don't pack unrelated tools into one server.
- Servers should run with the **least privilege** they need. A filesystem server shouldn't have network access.
- Resources (read-only data) and tools (actions) are different MCP concepts — model state as resources when read-only, even if you could implement it as a `get_*` tool.

See `templates/mcp_server_skeleton.py` for a minimal scaffold.

## Behavior when invoked

1. Enumerate the existing tools and their descriptions.
2. For each, check: narrow, named for verb, schema typed, output shape consistent, errors typed, side-effect class declared. Flag violations.
3. Identify redundant or overlapping tools to merge.
4. Identify god-tools to split.
5. For new tools the user wants to add, walk through the five properties before they write the schema.

## What this skill will NOT do

- Add a "do_anything" or shell-passthrough tool. If the user insists, separate read and write surfaces and require `confirm: true` on writes.
- Accept untyped `dict` / `object` inputs as a shortcut for schema design.
- Wrap irreversible operations without making `dry_run` the default.
- Hide tool calls inside other tools. Every action the agent takes should be visible at the tool-call level.

## Templates

- `templates/tool_definition.example.py` — a well-designed tool (Anthropic SDK shape) with typed inputs, consistent envelope, typed errors, and idempotency.
- `templates/mcp_server_skeleton.py` — minimal MCP server with one resource and one tool, following the patterns above.
