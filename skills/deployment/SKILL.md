---
name: deployment
description: Scaffold deployment for AI agents — pinned model/prompt versions, secrets management, state outside the container, per-tenant cost caps, canary rollout, and health checks that actually verify the model. Use when moving an agent from "runs on my laptop" to anywhere else.
---

# deployment

Deploying an AI agent looks like deploying a service, but several things are different: behavior changes with the model version (which the platform can change under you), API keys are catastrophic to leak, costs are unbounded by default, and "healthy process" doesn't mean "healthy agent." This skill codifies the deltas. Pairs with `security-guardrails` (defenses at runtime), `logging` (the only way to debug prod), and `error-handling` (the only way to survive prod).

## When to trigger

- User types `/deployment`.
- User is about to ship an agent off their laptop — to staging, prod, a server, a container platform, or a SaaS host.
- User mentions "going live", "production", "users", or "first paying customer."
- An existing agent runs in prod but has no model pinning, no per-tenant cost cap, or stores state inside the container.

## Pin everything that affects behavior

A model upgrade you didn't initiate can change every output your agent produces. Treat the agent's behavior surface as a versioned bundle.

Pin and log on every run:

- **Model ID with version suffix** (e.g. `claude-opus-4-7`, not `claude-opus-latest`). "Latest" aliases are convenience; in prod they're a regression vector.
- **Prompt hash** — SHA of the rendered system prompt (see `prompt-engineering`).
- **Tool schema hash** — SHA of the combined tool definitions.
- **Eval baseline ID** — the eval run ID this build was validated against (see `evals`).
- **Code SHA** — git commit of the build.

A deployment is the tuple `(code_sha, model_id, prompt_hash, tools_hash, eval_baseline)`. Rolling back means restoring that tuple. Without it, "rollback" is a wish.

## Secrets

Treat model API keys like database passwords — because the blast radius is comparable (cost, data, reputation).

- **Never** in `.env` committed to the repo, never in image layers, never in build args.
- **Mount** from the platform secret store at runtime (Kubernetes Secrets, AWS Secrets Manager, GCP Secret Manager, Doppler, 1Password CLI, etc.).
- **Scope per environment.** Dev keys, staging keys, prod keys are distinct. If a dev key leaks, prod is unaffected.
- **Rotate on a schedule** (90 days max) and immediately on any incident.
- **Audit which key was used per request** — log the key's *fingerprint* (first 4 chars + last 4 chars), never the value.

If a key is committed, rotate it before you rewrite git history. Pushed history is already exfiltrated to anyone watching.

## State lives outside the container

Anything an agent needs after the container dies should not be inside it.

| State | Where it should live |
| --- | --- |
| Memory (`memory` skill) | Persistent volume, S3, or an external KV/DB |
| Audit logs | Log aggregator (CloudWatch, Datadog, Loki) |
| Eval results | Object storage with run-id keys |
| Idempotency cache | Redis or equivalent with TTL |
| Prompt CHANGELOG | Git, not the container |

If a deploy wipes state, it's stored wrong.

## Cost guardrails at deploy time

In a regular service, runaway code means slow responses. In an agent service, runaway code means a credit card bill. Caps must exist *before* launch, not after the first incident.

- **Per-tenant dollar cap** — daily and monthly. Hard-fail past the cap with a clear error.
- **Per-tenant rate limit** — requests per minute, tokens per minute. Independent of dollar cap.
- **Global circuit breaker** — if total spend in the last 5 minutes exceeds N× the rolling average, halt and page.
- **Per-run budget** — already covered in `error-handling`, but enforce at the entrypoint too.

Test these in staging by trying to exceed them. A cap you've never tripped doesn't work.

## Health checks that mean something

A process that responds to `/healthz` with `200 OK` may still be broken: the model API key expired, the model returns errors, every tool fails. A useful health check probes the actual capability.

- **Liveness**: process is up. Cheap.
- **Readiness**: can serve traffic. Includes:
  - Model client can authenticate (one cheap `messages.create` call to a Haiku-tier model, or a `count_tokens`).
  - Required tools/dependencies reachable.
  - Persistent state store reachable.
- **Synthetic transaction (every N minutes)**: run a fixed prompt through the full agent loop and assert the output matches a known pattern. This catches regressions a status code never would.

Cache the readiness result (~30s) so health checks don't themselves burn budget.

## Rollout strategy

Behavior changes with prompts and models, not just code. Roll out behavior the same way you'd roll out a feature.

- **Two environments minimum.** A staging that mirrors prod (same model version, same prompts, same tool schemas). Test there first; production is not your test bench.
- **Canary by deterministic hash, not random sample.** Hash on `user_id` so a given user sees a stable experience and bug reports are reproducible. Random sampling makes "it worked the first time" indistinguishable from "it sometimes works."
- **Bake time before full rollout.** Agents have long-tail failures that don't surface in the first hour. Hold canary for ≥24h on a non-trivial slice (5-10%) before going to 100%.
- **Watch for the right signals:** error rate, p95 latency, refusal rate (sudden refusals on benign input = regression), eval score on production traces, per-user cost p95.

## Rollback

A rollback is the deployment tuple from the last good build. Practice it.

- Roll forward, not in place. A "fix" deployed in panic is a second untested change. Restore the last good tuple, then debug.
- If prompts and code are versioned separately, you may need to roll back only the prompt — keep that mechanism distinct.
- Model deprecations are different — the old model is gone. Have a plan: pin to the next-best supported model and document the eval delta.

## Containerization patterns

For Python agents:

- **Multi-stage build.** Builder stage installs deps; final stage copies the venv and source. Final image stays small.
- **Non-root user.** Run as `app`, not `root`. Tools running as root can do far more damage if compromised.
- **No secrets in layers.** Never `ARG OPENAI_API_KEY` or `COPY .env`. Layers persist even if a later step deletes them.
- **Pin base image to a digest.** `python:3.12-slim@sha256:…` not `python:3.12-slim`. Tag rewrites happen.
- **Read-only filesystem** where possible (`docker run --read-only`), with explicit `tmpfs` mounts for cache.

See `templates/Dockerfile.python` and `templates/docker-compose.yml`.

## Environment configuration

- **One config schema, validated at startup.** Crash on missing or invalid env vars at boot, not on the first request that needs them.
- **Distinct env per environment.** `APP_ENV=dev|staging|prod` and behavior differs accordingly (e.g. dry-run defaults, sample rates, log levels).
- **No conditional code paths driven by hostname.** Set `APP_ENV` explicitly; the host can lie.

## Observability hookup

A deployment is not done until logs and traces flow to wherever the on-call person looks.

- **Ship the structured logs from `logging` skill to the platform's sink.** Not stdout-into-the-void.
- **Tag every log line with `app_env`, `code_sha`, `model_id`, `prompt_hash`.** These are the dimensions you'll filter on during an incident.
- **Wire the budget/retry counters to a metrics pipeline.** Per-tenant spend, retry attempts, escalation rate — all dashboarded.
- **Alerts before launch, not after the first 3am page.** Minimum: error-rate spike, cost-cap trip, model-API-error spike, p95-latency spike.

## Behavior when invoked

1. Detect target platform (local Docker, Compose, Kubernetes, serverless, PaaS) from existing config or by asking.
2. Inventory the deployment tuple: is the model pinned, are prompts/tools hashed, where do secrets come from, where does state live, are caps wired?
3. Produce a punch list of what's missing.
4. Scaffold a Dockerfile and a compose file (or platform-equivalent) that follow the patterns above.
5. Wire health checks that probe the model, not just the process.
6. Add cost-cap enforcement at the entrypoint.
7. Document the rollback tuple and the steps to restore it.

## What this skill will NOT do

- Use `latest` model aliases or unpinned base images.
- Bake secrets into images. Even "just for testing."
- Skip per-tenant cost caps to ship faster. The cap is part of shipping.
- Mix dev and prod credentials, even briefly.
- Use random-sample canaries (use deterministic hashing on user_id).
- Replace synthetic transaction health checks with simple `/healthz` 200 responses.

## Templates

- `templates/Dockerfile.python` — multi-stage Python build with non-root user, pinned base image digest placeholder, no secret args.
- `templates/docker-compose.yml` — minimal compose with persistent state volumes, secret mounts, healthcheck, and read-only root filesystem.
- `templates/config.example.py` — startup-validated config: required env vars, fail-fast on missing, fingerprinted secrets in logs.
