"""Expose a subagent as a typed tool from the parent's perspective.

Patterns (see ../SKILL.md):
  - Inputs are a typed schema, not free-form messages.
  - The subagent runs in its OWN context — it does not see the parent's
    conversation. It receives only what the schema passes in.
  - Output is a structured envelope (summary first, structured fields beside).
    The full subagent transcript is NOT returned — at most stored under a
    handle the parent can fetch on demand.
  - Same tool-design rules apply: idempotency where possible, timeouts,
    typed errors, no auto-retries on non-retryable codes.

Below: the schema you would register with the parent's model client, and a
runner function that performs the call with proper isolation.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

log = logging.getLogger(__name__)

Outcome = Literal["success", "failure", "timeout", "budget_exhausted"]


# In-memory handle store so subagent transcripts can be referenced without
# being inlined into the parent's context. In prod, back this with KV or blob.
_TRANSCRIPT_STORE: dict[str, list[dict[str, Any]]] = {}


# The schema the parent model sees and chooses to invoke.
RESEARCHER_SUBAGENT_TOOL = {
    "name": "researcher_subagent",
    "description": (
        "Spawn a focused research subagent that investigates a single question "
        "and returns a structured summary. Use when the question requires "
        "web/RAG retrieval AND multi-step reasoning that would otherwise crowd "
        "the parent agent's context. Do not use for one-shot lookups — call "
        "search_docs directly for those."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "One self-contained research question. 10-300 chars.",
            },
            "scope": {
                "type": "string",
                "enum": ["docs", "code", "web", "all"],
                "description": "Which corpus to search. Default 'docs'.",
            },
            "max_seconds": {
                "type": "integer",
                "description": "Hard wall-clock cap for this subagent. 5-120s. Required.",
            },
            "max_cost_usd": {
                "type": "number",
                "description": "Hard cost cap for this subagent. 0.01-1.00 USD. Required.",
            },
        },
        "required": ["question", "max_seconds", "max_cost_usd"],
    },
}


@dataclass
class SubagentEnvelope:
    ok: bool
    outcome: Outcome
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    transcript_handle: str | None = None
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    error_code: str | None = None

    def to_tool_result(self) -> dict[str, Any]:
        """Compact shape the parent model can reason over."""
        body = {
            "ok": self.ok,
            "summary": self.summary,
            "findings": self.findings,
            "metrics": {
                "elapsed_s": round(self.elapsed_s, 2),
                "cost_usd": round(self.cost_usd, 4),
            },
        }
        if self.transcript_handle:
            body["transcript_handle"] = self.transcript_handle
        if not self.ok:
            body["error"] = {"code": self.error_code or "internal_error", "outcome": self.outcome}
        return body


# An InnerLoop is the function that actually runs the subagent's model loop
# in isolation. Plug your own in — this template provides a stand-in.
InnerLoop = Callable[
    [str, str, float, float],  # question, scope, max_seconds, max_cost_usd
    Awaitable[tuple[list[dict[str, Any]], list[dict[str, Any]], float]],
]
# Returns: (findings, transcript, cost_usd)


def _validate(args: dict[str, Any]) -> tuple[str, str, float, float] | dict[str, Any]:
    question = (args.get("question") or "").strip()
    if not 10 <= len(question) <= 300:
        return {"code": "invalid_argument", "msg": "question must be 10-300 chars."}
    scope = args.get("scope", "docs")
    if scope not in ("docs", "code", "web", "all"):
        return {"code": "invalid_argument", "msg": "scope must be docs|code|web|all."}
    try:
        max_seconds = int(args["max_seconds"])
        if not 5 <= max_seconds <= 120:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"code": "invalid_argument", "msg": "max_seconds must be int in [5, 120]."}
    try:
        max_cost_usd = float(args["max_cost_usd"])
        if not 0.01 <= max_cost_usd <= 1.00:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"code": "invalid_argument", "msg": "max_cost_usd must be in [0.01, 1.00]."}
    return question, scope, max_seconds, max_cost_usd


async def run_researcher_subagent(
    args: dict[str, Any],
    inner_loop: InnerLoop,
    *,
    parent_trace_id: str,
) -> dict[str, Any]:
    """Run a researcher subagent and return the compact tool-result body.

    Behavior:
      - Validates inputs at the boundary.
      - Runs the subagent in an ISOLATED context (the parent's transcript is
        never passed in).
      - Stores the full transcript under a handle; only the summary +
        structured findings come back inline.
      - Maps all failure modes onto typed outcome / error_code.
    """
    validated = _validate(args)
    if isinstance(validated, dict):
        return SubagentEnvelope(
            ok=False, outcome="failure",
            summary=validated["msg"], error_code=validated["code"],
        ).to_tool_result()

    question, scope, max_seconds, max_cost_usd = validated
    span_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    log.info(
        "subagent_start",
        extra={
            "trace_id": parent_trace_id,
            "span_id": span_id,
            "agent_role": "researcher",
            "scope": scope,
            "max_seconds": max_seconds,
            "max_cost_usd": max_cost_usd,
        },
    )

    try:
        findings, transcript, cost_usd = await inner_loop(question, scope, max_seconds, max_cost_usd)
    except TimeoutError:
        return SubagentEnvelope(
            ok=False, outcome="timeout",
            summary=f"Researcher exceeded {max_seconds}s.",
            elapsed_s=time.monotonic() - started,
            error_code="timeout",
        ).to_tool_result()
    except Exception as e:
        return SubagentEnvelope(
            ok=False, outcome="failure",
            summary=f"Researcher raised {type(e).__name__}.",
            elapsed_s=time.monotonic() - started,
            error_code="internal_error",
        ).to_tool_result()

    handle = f"transcript:{span_id}"
    _TRANSCRIPT_STORE[handle] = transcript
    env = SubagentEnvelope(
        ok=True,
        outcome="success",
        summary=_summarize(question, findings),
        findings=findings,
        transcript_handle=handle,
        cost_usd=cost_usd,
        elapsed_s=time.monotonic() - started,
    )
    log.info(
        "subagent_done",
        extra={
            "trace_id": parent_trace_id,
            "span_id": span_id,
            "elapsed_s": round(env.elapsed_s, 2),
            "cost_usd": round(env.cost_usd, 4),
            "findings_count": len(findings),
        },
    )
    return env.to_tool_result()


def _summarize(question: str, findings: list[dict[str, Any]]) -> str:
    if not findings:
        return f"No findings for: {question}"
    return f"{len(findings)} findings for: {question}"


def fetch_transcript(handle: str) -> list[dict[str, Any]] | None:
    """The parent may call this on demand if it needs the raw subagent work."""
    return _TRANSCRIPT_STORE.get(handle)
