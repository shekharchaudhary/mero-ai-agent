---
name: cost-tracking
description: Scaffold cost attribution and enforcement for AI agents — a per-call ledger with tenant/run attribution, price-versioned cost math, real-time budget caps, cache-hit-rate monitoring, and cost-per-outcome reporting. Use before launch, not after the first surprising invoice.
---

# cost-tracking

A cost-tracking system is the difference between knowing what each request cost and reading it off the provider invoice a month later. This skill ties together what other skills already touch: `logging` records the tokens, `error-handling` caps the per-run budget, `deployment` caps the per-tenant budget, `prompt-engineering` controls the cache. Cost-tracking is the ledger and enforcement layer that makes those numbers coherent.

## When to trigger

- User types `/cost-tracking`.
- User is about to launch and has no per-tenant attribution.
- User says "the bill was higher than expected" / "I don't know which feature spent the most" / "who's our biggest user by cost."
- An agent project logs tokens but doesn't aggregate them or doesn't compute dollars.

## What to track on every model call

These six fields are the minimum. Drop any and you can't attribute or compare.

| Field | Why |
| --- | --- |
| `ts` | Time of call (UTC, ISO-8601). |
| `trace_id` / `tenant_id` / `user_id` | Attribution. Without these, you have a single bill, not a ledger. |
| `model_id` | Pricing varies per model and version. |
| `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens` | The four numbers that make up cost. Tracking only `input + output` hides the cache picture. |
| `price_version` | Which pricing table produced the `cost_usd` for this entry. Critical for historical accuracy after a price change. |
| `cost_usd` | Computed at write time. Don't compute lazily — at write time the price is known and stable. |

Optional but high-value: `feature` (which product surface called this), `outcome` (did the run succeed), `latency_ms`. With `feature` and `outcome` you can compute cost-per-outcome by feature, which is the number that actually matters.

## Track at the call site, not after the fact

Reconciling token counts from logs days later is reactive. Compute and record cost in the same function that makes the API call, in the same transaction. Tradeoffs:

- **In-process ledger.** Cheap, fast. Risk: lose data on crash. Mitigation: flush every N calls and on shutdown.
- **External KV/DB.** Durable, queryable. Risk: blocks the request path. Mitigation: write asynchronously.
- **Append-only file.** A middle ground. Easy to ship to a log sink later. See `templates/cost_ledger.py`.

Pick durable + async for production. The ledger is your billing source of truth — losing entries means losing money and trust.

## Pricing tables and versioning

Anthropic publishes per-model prices in dollars per million tokens with separate rates for input, output, cache write, and cache read. Two rules:

1. **Snapshot prices to a versioned table** in your code. Don't fetch live — a network blip during a cost calculation must not block the request.
2. **Stamp every ledger entry with the `price_version`** that produced its `cost_usd`. When prices change, the new entries use the new table; historical entries stay accurate. Dashboards that aggregate across versions show the right total without retroactive rewrites.

See `templates/pricing.py` for the shape — it ships with a dated snapshot **you must verify** against the current Anthropic pricing page before using.

## Real-time enforcement

A cap that you check after the call has already happened is a refund request, not a control. Enforce before the call:

- **Per-run cap** (from `error-handling`): reject the call before it's made if it would exceed the run budget. Estimate cost from a token-counting pre-pass on the prompt.
- **Per-tenant cap** (daily + monthly, from `deployment`): query the ledger's running total for the tenant; reject if `current + estimated > cap`.
- **Per-feature cap**: useful when a low-value feature shouldn't crowd out a high-value one.
- **Global circuit breaker**: if cluster-wide spend in the last 5 minutes is N× the rolling average, halt new requests and page on-call.

Order matters: cheapest check first (per-run, in-memory) before the expensive check (per-tenant lookup). And every reject must surface a typed error (`rate_limited` / `budget_exhausted` — see `tool-design`) the caller can show to the user.

## Cache is a cost lever, watch it

A 90% cache hit rate vs a 10% one is roughly an 8x cost difference on cached layers. The cache is silent — if it breaks, costs rise but nothing else changes. Track these as first-class metrics:

- **Cache hit rate** = `cache_read_tokens / (cache_read_tokens + cache_creation_tokens + uncached_input_tokens)`.
- **Hot-path hit rate**, by `feature` or `endpoint`. Aggregate hit rate hides a single broken path.
- **Cache miss reasons**, when known. The most common: a timestamp or session id interpolated into the prefix.

Alert thresholds:

- Hit rate drops more than 20pp from the rolling weekly mean on a hot path → page someone.
- Cache creation tokens spike but reads stay flat → prefix is changing every call.

See `prompt-engineering` for the layout that keeps cache hot.

## Cost per outcome, not cost per request

Two agents that cost the same per request are not equally expensive. The one that succeeds in 1 run vs the one that takes 4 retries costs 4×. Track outcomes alongside cost:

- For each run, label the outcome: `success`, `failure`, `partial`, `user_aborted`, `budget_exhausted`.
- Dashboard `cost_per_successful_run` by feature. This is the number that should drive optimization decisions.
- A model upgrade that doubles per-call cost but halves retries can be a net win — only cost-per-outcome reveals it.

## Burn rate and forecasting

Once the ledger has a few weeks of data:

- **Rolling 24h burn rate.** The simplest signal — alarming spikes are usually visible here first.
- **Projected month-end spend** = `current_month_spend + (days_remaining × rolling_daily_avg)`. Surface in the same place caps are configured so the operator sees both.
- **Per-tenant trajectory.** Tenants approaching their monthly cap need a soft warning before they hit it (e.g. notify at 80%, hard-fail at 100%).

## Common cost traps

| Trap | Smell | Fix |
| --- | --- | --- |
| Uncached prefix | Cache creation tokens ≈ input tokens on every call | Move variable content after the cache breakpoint (`prompt-engineering`) |
| Runaway loop | Cost-per-run p95 ≫ p50 for one feature | Repeat-call detector (`error-handling`) |
| Expensive judge in evals | Eval CI cost > agent prod cost | Sample dev runs; reserve full evals for CI of releases |
| Debug logging full prompts | Storage cost > token cost; PII risk | Log summaries, not bodies (`logging`) |
| Wrong-model defaults | Opus where Haiku would do | Route by task complexity; reserve Opus for steps that need it |
| No per-tenant attribution | "Who is spending all of this?" is unanswerable | Add `tenant_id` to every entry; backfill is hard, ship it on day one |

## Reporting

Three views cover most needs:

1. **Per-tenant rollup** — daily and month-to-date spend, projected month-end, cap status.
2. **Per-feature rollup** — daily and month-to-date spend with cost-per-outcome.
3. **Anomaly view** — runs where `cost_usd` exceeds the p95 for their feature. Most cost incidents show up here first.

Build these as SQL on the ledger table (or notebook queries over the JSONL). Don't outsource the source of truth to a third-party dashboard; build views on top of your own ledger.

## Behavior when invoked

1. Audit: where do token counts currently land? Is there attribution? Is `cost_usd` ever computed?
2. Install a price-versioned pricing table and a ledger writer at the model-call boundary.
3. Wire pre-call enforcement: per-run cap, per-tenant cap, global circuit breaker.
4. Add cache-hit-rate metric to the standard log enrichment.
5. Tag each run with its outcome so cost-per-outcome can be computed.
6. Ship the three reporting views.

## What this skill will NOT do

- Compute cost lazily from `total_tokens × one_price`. The four token types have different prices.
- Hardcode prices without a `price_version`. Prices change; historical entries must remain accurate.
- Replace per-call attribution with monthly aggregates. Aggregates can be derived from entries; entries cannot be derived from aggregates.
- Fetch prices over the network on the request path.
- Trust provider-side dashboards as the source of truth for per-tenant attribution. Build your own ledger.
- Add tracking that itself becomes a significant cost (e.g. a model-based "classifier" labeling every entry).

## Templates

- `templates/pricing.py` — versioned pricing table (snapshot dated; verify against current pricing before use) with a `cost_usd_for(usage, model_id)` helper that returns cost + `price_version`.
- `templates/cost_ledger.py` — append-only ledger writer with attribution fields, async flush, per-tenant accumulator, pre-call cap check, and helpers for cache-hit-rate and cost-per-outcome.
