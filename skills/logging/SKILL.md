---
name: logging
description: Scaffold structured logging for AI agent projects — JSON logs, trace IDs across tool calls, token/cost tracking, and PII redaction. Use when starting a new agent project or when an existing one is logging via print/console.
---

# logging

Sets up observable, structured logging for an AI agent so runs are debuggable after the fact and costs are attributable per request. Pairs with `security-guardrails` — that skill handles the audit/integrity angle; this skill handles the developer-debug angle.

## When to trigger

- User types `/logging`.
- User asks to "add logging", "instrument", "see what the agent is doing", or "track token usage".
- Existing code uses `print()` / `console.log` for agent activity.

## What it sets up

### 1. Structured JSON logs

One JSON object per line. Never multi-line. Required fields on every record:

- `ts` — ISO-8601 timestamp with timezone.
- `level` — `debug` | `info` | `warn` | `error`.
- `event` — short snake_case name (`tool_call_start`, `model_response`, `run_complete`).
- `trace_id` — UUID generated at the start of each agent run.
- `span_id` — UUID per tool call or model call, with `parent_span_id` for nesting.

### 2. What to log

| Event | Level | Fields |
| --- | --- | --- |
| `run_start` | info | trace_id, user_prompt_hash, model |
| `model_call` | info | span_id, model, input_tokens, cache_read_tokens, latency_ms |
| `model_response` | info | span_id, output_tokens, stop_reason, latency_ms |
| `tool_call` | info | span_id, tool_name, args (redacted), duration_ms, result_summary |
| `tool_error` | error | span_id, tool_name, error_type, error_msg |
| `permission_denied` | warn | tool_name, reason |
| `run_complete` | info | trace_id, total_tokens, total_cost_usd, duration_ms, outcome |

Log the **summary** of tool results, not the full body — full bodies belong in a separate trace store, not the main log stream.

### 3. PII and secret redaction

Redact before serialization, not after:

- Keys to drop entirely: `api_key`, `authorization`, `password`, `secret`, `token`, `cookie`, `set-cookie`.
- Values matching common patterns: bearer tokens, AWS keys (`AKIA…`), JWT (`eyJ…`), private keys (`-----BEGIN`).
- Long strings (>200 chars) in unexpected fields → truncate with `…[truncated 1234 chars]`.

### 4. Token and cost tracking

- Record per-call: `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`.
- Compute `cost_usd` from the model's published price; store the price version used so historical costs stay reproducible after price changes.
- Emit a `run_complete` aggregate so dashboards can group by trace_id.

### 5. Log destinations

- **Local dev**: stdout (JSON) piped through `jq` for human reading.
- **Production**: file in append mode with daily rotation, plus shipping to the platform's log sink (CloudWatch, Datadog, Loki).
- Never log to a file inside the project directory — the agent may overwrite or delete its own logs. Use `~/.agent-logs/<project>/` or `/var/log/<service>/`.

### 6. Sampling

- Log every `run_start`, `run_complete`, `*_error`, `permission_denied` — always.
- For `debug`-level events in high-volume paths, sample at 1–10% with `trace_id`-based sticky sampling so all events for a sampled run are kept together.

## Behavior when invoked

1. Detect the project's language (Python, TypeScript, Go) and existing logger if any.
2. Inspect what is currently being logged; identify gaps against the table above.
3. Offer to install/wire a structured logger using the language's idiomatic library (`structlog` for Python, `pino` for Node, `slog` for Go).
4. Add the redaction layer before any handler.
5. Replace `print()` / `console.log` calls with structured events — show the user the diff before applying.

## What this skill will NOT do

- Log raw secrets, bearer tokens, or private keys, even if the user asks. Redact and log the fact that a value was redacted.
- Log full tool inputs/outputs by default — too easy to leak large payloads or secrets. Provide a separate, opt-in trace store for that.
- Replace `print()` calls in unrelated code paths (tests, CLI tools, scripts) — agent code only.

## Templates

- `templates/structured_logger.py` — minimal `structlog` setup with redaction and trace context.
