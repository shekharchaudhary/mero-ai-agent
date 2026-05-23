"""Durable review queue for human-in-the-loop approvals.

Patterns (see ../SKILL.md):
  - Durable: persisted to JSONL on every state change (replace with KV/DB in prod).
  - Idempotent enqueue: same dedup_key returns the existing request, doesn't duplicate.
  - Priority queue: irreversible > policy > confidence > sample.
  - Explicit per-action-class timeout policy: default_allow / default_deny / default_defer.
  - Audit-logged: every state transition recorded with who/when/why.

This is not a high-throughput queue; it's the right level of abstraction for
agent approvals (~hundreds to low thousands per day). For higher volume,
swap the JSONL persistence for a real queue (SQS, Redis Streams, Postgres).

Usage:
    queue = ReviewQueue(path="/var/agent/reviews.jsonl", policies={
        "irreversible_write": TimeoutPolicy(seconds=600, on_timeout="default_deny"),
        "reversible_write":   TimeoutPolicy(seconds=300, on_timeout="default_allow"),
        "read":               TimeoutPolicy(seconds=120, on_timeout="default_allow"),
    })

    req = queue.enqueue(
        trace_id="r-1", tenant_id="t-1", action_name="send_email",
        action_class="irreversible_write", reasons=["irreversible_write"],
        payload={"to": "...", "subject": "..."},
        dedup_key="r-1:send_email:to=...",
    )
    # ... reviewer interaction happens out-of-band ...
    queue.decide(req.id, reviewer_id="alice", decision="approve", note="LGTM")
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

Decision = Literal["approve", "deny", "edit", "defer"]
Status = Literal["pending", "approved", "denied", "edited", "deferred", "timed_out"]
Priority = Literal["irreversible", "policy", "confidence", "sample"]
ActionClass = Literal["read", "reversible_write", "irreversible_write"]
TimeoutAction = Literal["default_allow", "default_deny", "default_defer"]

_PRIORITY_ORDER = {"irreversible": 0, "policy": 1, "confidence": 2, "sample": 3}


@dataclass(frozen=True)
class TimeoutPolicy:
    seconds: float
    on_timeout: TimeoutAction


@dataclass
class ReviewRequest:
    id: str
    trace_id: str
    tenant_id: str
    action_name: str
    action_class: ActionClass
    priority: Priority
    reasons: list[str]
    payload: dict[str, Any]
    dedup_key: str
    requires_two_person: bool
    created_at: float
    status: Status = "pending"
    decisions: list[dict[str, Any]] = field(default_factory=list)
    final: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _priority_for(action_class: ActionClass, reasons: list[str]) -> Priority:
    if action_class == "irreversible_write":
        return "irreversible"
    if any(r.startswith("policy") for r in reasons):
        return "policy"
    if any("disagreement" in r or "confidence" in r or "novelty" in r for r in reasons):
        return "confidence"
    return "sample"


class ReviewQueue:
    def __init__(
        self,
        path: str,
        policies: dict[ActionClass, TimeoutPolicy],
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.policies = policies
        self._lock = threading.Lock()
        self._by_id: dict[str, ReviewRequest] = {}
        self._by_dedup: dict[str, str] = {}
        self._load()

    # --- Persistence ---

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = entry.get("kind")
            if kind == "request":
                r = ReviewRequest(**entry["data"])
                self._by_id[r.id] = r
                self._by_dedup[r.dedup_key] = r.id
            elif kind == "decision":
                r = self._by_id.get(entry["request_id"])
                if r:
                    r.decisions.append(entry["data"])
                    if entry.get("final"):
                        r.status = entry["data"]["status"]
                        r.final = entry["data"]

    def _persist(self, kind: str, **fields: Any) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, "ts": time.time(), **fields}, default=str) + "\n")

    # --- API ---

    def enqueue(
        self,
        *,
        trace_id: str,
        tenant_id: str,
        action_name: str,
        action_class: ActionClass,
        reasons: list[str],
        payload: dict[str, Any],
        dedup_key: str,
        requires_two_person: bool = False,
    ) -> ReviewRequest:
        with self._lock:
            if dedup_key in self._by_dedup:
                existing = self._by_id[self._by_dedup[dedup_key]]
                log.info(
                    "review_enqueue_dedup",
                    extra={"request_id": existing.id, "dedup_key": dedup_key},
                )
                return existing
            req = ReviewRequest(
                id=uuid.uuid4().hex[:12],
                trace_id=trace_id,
                tenant_id=tenant_id,
                action_name=action_name,
                action_class=action_class,
                priority=_priority_for(action_class, reasons),
                reasons=reasons,
                payload=payload,
                dedup_key=dedup_key,
                requires_two_person=requires_two_person,
                created_at=time.time(),
            )
            self._by_id[req.id] = req
            self._by_dedup[dedup_key] = req.id
            self._persist("request", data=req.to_dict())
            log.info(
                "review_enqueued",
                extra={
                    "request_id": req.id, "trace_id": trace_id,
                    "priority": req.priority, "action_class": action_class,
                    "two_person": requires_two_person,
                },
            )
            return req

    def get(self, request_id: str) -> ReviewRequest | None:
        return self._by_id.get(request_id)

    def pending(self) -> list[ReviewRequest]:
        items = [r for r in self._by_id.values() if r.status == "pending"]
        items.sort(key=lambda r: (_PRIORITY_ORDER[r.priority], r.created_at))
        return items

    def decide(
        self,
        request_id: str,
        *,
        reviewer_id: str,
        decision: Decision,
        note: str | None = None,
        edited_payload: dict[str, Any] | None = None,
    ) -> ReviewRequest:
        with self._lock:
            req = self._by_id.get(request_id)
            if req is None:
                raise KeyError(f"Unknown request_id: {request_id}")
            if req.status != "pending":
                raise ValueError(f"Request {request_id} is not pending (status={req.status})")

            already = {d["reviewer_id"] for d in req.decisions}
            if reviewer_id in already:
                raise ValueError(f"Reviewer {reviewer_id} already decided on {request_id}")

            entry = {
                "reviewer_id": reviewer_id,
                "decision": decision,
                "note": note,
                "ts": time.time(),
            }
            req.decisions.append(entry)
            self._persist("decision", request_id=request_id, data=entry, final=False)

            # Resolve final status: two-person requires two distinct approvals;
            # any single deny is terminal.
            if decision == "deny":
                final = {"status": "denied", "by": reviewer_id, "note": note, "ts": time.time()}
            elif decision == "edit":
                final = {
                    "status": "edited", "by": reviewer_id, "note": note,
                    "edited_payload": edited_payload, "ts": time.time(),
                }
            elif decision == "defer":
                final = {"status": "deferred", "by": reviewer_id, "note": note, "ts": time.time()}
            elif decision == "approve":
                approvals = [d for d in req.decisions if d["decision"] == "approve"]
                if req.requires_two_person and len(approvals) < 2:
                    return req  # need another approver
                final = {"status": "approved", "by": [d["reviewer_id"] for d in approvals], "ts": time.time()}
            else:
                raise ValueError(f"Unknown decision: {decision}")

            req.status = final["status"]  # type: ignore[assignment]
            req.final = final
            self._persist("decision", request_id=request_id, data=final, final=True)
            log.info(
                "review_resolved",
                extra={"request_id": request_id, "status": req.status, "reviewer_id": reviewer_id},
            )
            return req

    def expire_overdue(self, now: float | None = None) -> list[ReviewRequest]:
        """Resolve any pending requests past their timeout per policy. Returns the timed-out ones."""
        now = now or time.time()
        timed_out: list[ReviewRequest] = []
        with self._lock:
            for req in self._by_id.values():
                if req.status != "pending":
                    continue
                policy = self.policies.get(req.action_class)
                if policy is None:
                    continue
                age = now - req.created_at
                if age < policy.seconds:
                    continue
                final = {
                    "status": "timed_out",
                    "default_action": policy.on_timeout,
                    "ts": now,
                }
                req.status = "timed_out"
                req.final = final
                self._persist("decision", request_id=req.id, data=final, final=True)
                timed_out.append(req)
                log.warning(
                    "review_timed_out",
                    extra={
                        "request_id": req.id,
                        "action_class": req.action_class,
                        "default_action": policy.on_timeout,
                        "age_s": round(age, 1),
                    },
                )
        return timed_out
