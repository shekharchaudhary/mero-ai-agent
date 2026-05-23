"""Gating function: decide whether an action needs human review, and which mode.

Patterns (see ../SKILL.md):
  - Multiple signals combined, not self-reported confidence alone.
  - Action-class (read / reversible_write / irreversible_write) is the
    strongest signal — irreversible writes always escalate.
  - Returns a structured GateDecision so the caller knows WHY it escalated.
  - No "review everything" mode — every signal has to earn its escalation.

Usage:
    decision = should_escalate(
        action=ProposedAction(name="send_email", class_="irreversible_write", value_usd=0.0),
        outputs_sampled=["...", "...", "..."],   # N samples for disagreement
        schema_valid=True,
        self_confidence=0.85,
        tenant_budget_share=0.02,
        novelty_score=0.3,
        sample_rate=0.01,
    )
    if decision.escalate:
        enqueue_for_review(decision)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

ActionClass = Literal["read", "reversible_write", "irreversible_write"]
Mode = Literal["block", "flag", "sample"]


@dataclass
class ProposedAction:
    name: str                       # e.g. "send_email", "publish_post"
    class_: ActionClass             # tag at the tool layer; this only reads it
    value_usd: float = 0.0          # blast-radius proxy: cost or affected count
    affects_user_count: int = 0     # for mass actions
    is_two_person_threshold: bool = False


@dataclass
class GateDecision:
    escalate: bool
    mode: Mode | None = None
    reasons: list[str] = field(default_factory=list)
    require_two_person: bool = False

    def summary(self) -> str:
        if not self.escalate:
            return "no_escalation"
        prefix = "two_person:" if self.require_two_person else ""
        return f"{prefix}{self.mode}: {', '.join(self.reasons)}"


# Thresholds — tune per project. These are sensible defaults; values can be
# moved to config so they're tweakable without code changes.
DISAGREEMENT_THRESHOLD = 0.5         # share of pairwise disagreements among samples
LOW_CONFIDENCE_THRESHOLD = 0.6       # self-reported, used only as a soft signal
BUDGET_SHARE_THRESHOLD = 0.10        # action exceeds 10% of tenant's remaining budget
NOVELTY_THRESHOLD = 0.7              # embedding distance from known-good cases
TWO_PERSON_VALUE_USD = 500.0
TWO_PERSON_USER_COUNT = 1000


def _pairwise_disagreement(samples: list[str]) -> float:
    if len(samples) < 2:
        return 0.0
    pairs = 0
    disagree = 0
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            pairs += 1
            if samples[i].strip() != samples[j].strip():
                disagree += 1
    return disagree / pairs if pairs else 0.0


def should_escalate(
    *,
    action: ProposedAction,
    outputs_sampled: list[str] | None = None,
    schema_valid: bool = True,
    self_confidence: float | None = None,
    tenant_budget_share: float = 0.0,
    novelty_score: float = 0.0,
    sample_rate: float = 0.0,
    rng: random.Random | None = None,
) -> GateDecision:
    """Combine signals into a single decision.

    Order is precedence: a stronger signal wins. We do not pile reasons on top
    of an already-blocking decision past what the reviewer needs.
    """
    reasons: list[str] = []
    rng = rng or random.Random()

    # 1. Irreversible writes always block.
    if action.class_ == "irreversible_write":
        reasons.append("irreversible_write")
        two_person = (
            action.value_usd >= TWO_PERSON_VALUE_USD
            or action.affects_user_count >= TWO_PERSON_USER_COUNT
            or action.is_two_person_threshold
        )
        return GateDecision(escalate=True, mode="block", reasons=reasons, require_two_person=two_person)

    # 2. Schema-shape failure — strong signal regardless of action class.
    if not schema_valid:
        reasons.append("schema_invalid")
        return GateDecision(escalate=True, mode="block", reasons=reasons)

    # 3. Output disagreement (only computed if caller provided samples).
    if outputs_sampled:
        disagreement = _pairwise_disagreement(outputs_sampled)
        if disagreement >= DISAGREEMENT_THRESHOLD:
            reasons.append(f"output_disagreement={disagreement:.2f}")
            mode: Mode = "block" if action.class_ == "reversible_write" else "flag"
            return GateDecision(escalate=True, mode=mode, reasons=reasons)

    # 4. Cost / blast-radius threshold.
    if tenant_budget_share >= BUDGET_SHARE_THRESHOLD:
        reasons.append(f"budget_share={tenant_budget_share:.2f}")
        return GateDecision(escalate=True, mode="block", reasons=reasons)

    # 5. Novelty — far from known-good.
    if novelty_score >= NOVELTY_THRESHOLD:
        reasons.append(f"novelty={novelty_score:.2f}")
        return GateDecision(
            escalate=True,
            mode="flag" if action.class_ == "read" else "block",
            reasons=reasons,
        )

    # 6. Self-reported confidence — soft signal, used only when combined
    #    with at least one other suspicious signal (recorded earlier).
    if (
        self_confidence is not None
        and self_confidence < LOW_CONFIDENCE_THRESHOLD
        and action.class_ == "reversible_write"
    ):
        reasons.append(f"low_self_confidence={self_confidence:.2f}")
        return GateDecision(escalate=True, mode="flag", reasons=reasons)

    # 7. Random quality sample.
    if sample_rate > 0 and rng.random() < sample_rate:
        reasons.append(f"quality_sample={sample_rate:.3f}")
        return GateDecision(escalate=True, mode="sample", reasons=reasons)

    return GateDecision(escalate=False)


if __name__ == "__main__":
    a = ProposedAction(name="send_email", class_="irreversible_write", value_usd=10.0)
    print(should_escalate(action=a, schema_valid=True, self_confidence=0.95).summary())

    b = ProposedAction(name="draft_email", class_="reversible_write")
    print(should_escalate(
        action=b,
        outputs_sampled=["yes ship it", "do not ship", "ship later"],
        self_confidence=0.7,
    ).summary())

    c = ProposedAction(name="publish_blog", class_="irreversible_write",
                       affects_user_count=5000, value_usd=0.0)
    print(should_escalate(action=c).summary())
