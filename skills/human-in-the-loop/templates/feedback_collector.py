"""Structured feedback capture with explicit downstream consumers.

Patterns (see ../SKILL.md):
  - Every feedback signal is tagged with WHERE it will go (eval / memory / metric / ignore).
  - Redaction applied at write time (same posture as logging skill).
  - "Collected but never used" is a failure mode — `route()` is the named
    consumer pipeline. If a signal has no consumer registered, the collector
    refuses to accept it.

Usage:
    collector = FeedbackCollector(redact=my_redactor)
    collector.register_consumer("eval", my_eval_appender)
    collector.register_consumer("memory", my_memory_writer)

    collector.capture(
        trace_id="r-1",
        source="reviewer",
        kind="edit",
        original={"to": "...", "subject": "X"},
        corrected={"to": "...", "subject": "X (cleaner)"},
        rationale="subject was too vague",
        destinations=["eval"],
        consent={"retain_for_evals": True, "retain_for_memory": False},
    )
    collector.route()  # fan out to registered consumers
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

log = logging.getLogger(__name__)

Source = Literal["reviewer", "end_user", "auto"]
Kind = Literal["approve", "deny", "edit", "rating", "freeform"]
Destination = Literal["eval", "memory", "metric", "ignore"]


@dataclass(frozen=True)
class Consent:
    retain_for_evals: bool = False
    retain_for_memory: bool = False


Redactor = Callable[[dict[str, Any]], dict[str, Any]]
Consumer = Callable[["FeedbackRecord"], None]


@dataclass
class FeedbackRecord:
    id: str
    ts: float
    trace_id: str
    source: Source
    kind: Kind
    payload: dict[str, Any]
    destinations: list[Destination]
    consent: dict[str, bool]
    routed_to: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "ts": self.ts, "trace_id": self.trace_id,
            "source": self.source, "kind": self.kind,
            "payload": self.payload, "destinations": self.destinations,
            "consent": self.consent, "routed_to": self.routed_to,
        }


class FeedbackCollector:
    """Captures structured feedback and routes it to registered consumers.

    Refuses to accept a destination with no registered consumer — by design,
    so 'feedback collected but never used' becomes a startup error, not a
    quiet quality bug.
    """

    def __init__(self, *, store_path: str | None = None, redact: Redactor | None = None) -> None:
        self.store_path = Path(store_path) if store_path else None
        if self.store_path:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.redact = redact or (lambda d: d)
        self._consumers: dict[Destination, Consumer] = {}
        self._records: list[FeedbackRecord] = []

    def register_consumer(self, dest: Destination, consumer: Consumer) -> None:
        self._consumers[dest] = consumer

    def capture(
        self,
        *,
        trace_id: str,
        source: Source,
        kind: Kind,
        destinations: list[Destination],
        consent: Consent | None = None,
        **payload: Any,
    ) -> FeedbackRecord:
        # Reject destinations that have no registered consumer (other than 'ignore').
        for d in destinations:
            if d == "ignore":
                continue
            if d not in self._consumers:
                raise ValueError(
                    f"Destination {d!r} has no registered consumer — "
                    f"register one with register_consumer({d!r}, ...) or remove it. "
                    f"Collected-but-never-used feedback is a smell."
                )
        # Enforce consent gates BEFORE writing.
        consent = consent or Consent()
        if "memory" in destinations and not consent.retain_for_memory:
            raise PermissionError("Consent.retain_for_memory must be True to route to memory.")
        if "eval" in destinations and not consent.retain_for_evals:
            raise PermissionError("Consent.retain_for_evals must be True to route to eval.")

        clean_payload = self.redact(payload)
        rec = FeedbackRecord(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            trace_id=trace_id,
            source=source,
            kind=kind,
            payload=clean_payload,
            destinations=destinations,
            consent={"retain_for_evals": consent.retain_for_evals,
                     "retain_for_memory": consent.retain_for_memory},
        )
        self._records.append(rec)
        self._persist(rec)
        log.info(
            "feedback_captured",
            extra={"feedback_id": rec.id, "trace_id": trace_id, "kind": kind, "destinations": destinations},
        )
        return rec

    def route(self) -> int:
        """Fan out unrouted records to their consumers. Returns count routed.

        Routing is idempotent — a record already routed to destination D is
        not re-sent. Records with a destination's consumer raising are left
        un-marked so they can be retried.
        """
        routed = 0
        for rec in self._records:
            for d in rec.destinations:
                if d == "ignore" or d in rec.routed_to:
                    continue
                consumer = self._consumers.get(d)
                if not consumer:
                    continue
                try:
                    consumer(rec)
                except Exception as e:
                    log.warning(
                        "feedback_route_failed",
                        extra={"feedback_id": rec.id, "destination": d, "error_type": type(e).__name__},
                    )
                    continue
                rec.routed_to.append(d)
                self._persist(rec, event="routed", destination=d)
                routed += 1
        return routed

    def _persist(self, rec: FeedbackRecord, **extra: Any) -> None:
        if not self.store_path:
            return
        with self.store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**rec.to_dict(), **extra}, default=str) + "\n")
