"""Bounded agent loop with no-progress detection and structured escalation.

Wraps the standard "model → tool → model" loop with three safety nets:

  1. Turn cap (hard ceiling)
  2. Repeat-call detector (same tool + args three times = stuck)
  3. No-state-change detector (scratchpad unchanged for N turns = stuck)

When any safety net trips, the loop exits with an EscalationRequired so
the caller can surface a structured message to the user rather than
silently looping.

This is a skeleton — wire in your model client and tool dispatch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class LoopConfig:
    max_turns: int = 30
    max_wall_clock_s: float = 300.0
    max_cost_usd: float = 5.0
    repeat_call_window: int = 5
    repeat_call_threshold: int = 3
    no_progress_turns: int = 5


@dataclass
class LoopState:
    turn: int = 0
    started_at: float = field(default_factory=time.monotonic)
    cost_usd: float = 0.0
    scratchpad_hash: str = ""
    scratchpad_unchanged_turns: int = 0
    recent_calls: deque[str] = field(default_factory=lambda: deque(maxlen=5))


class EscalationRequired(Exception):
    """Raised when the loop must stop and surface to the user."""

    def __init__(self, reason: str, state: LoopState, recent_trace: list[dict[str, Any]]) -> None:
        self.reason = reason
        self.state = state
        self.recent_trace = recent_trace
        super().__init__(self.summary())

    def summary(self) -> str:
        return (
            f"Stopping agent loop: {self.reason}. "
            f"turn={self.state.turn}, elapsed={time.monotonic() - self.state.started_at:.1f}s, "
            f"cost=${self.state.cost_usd:.4f}."
        )

    def user_message(self) -> str:
        """A structured 'I'm stuck' message the caller can show the user."""
        return (
            f"I stopped because: {self.reason}.\n\n"
            f"Last few steps:\n"
            + "\n".join(f"  - {t.get('tool', '?')}({_short(t.get('args'))})" for t in self.recent_trace[-3:])
            + "\n\nWant me to try a different approach, or do you have more context?"
        )


def _short(obj: Any, n: int = 60) -> str:
    s = json.dumps(obj, default=str, sort_keys=True) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[: n - 1] + "…"


def _call_hash(tool_name: str, args: dict[str, Any]) -> str:
    payload = json.dumps({"t": tool_name, "a": args}, default=str, sort_keys=True).encode()
    return hashlib.sha1(payload).hexdigest()[:12]


def _scratchpad_hash(scratchpad: Any) -> str:
    payload = json.dumps(scratchpad, default=str, sort_keys=True).encode()
    return hashlib.sha1(payload).hexdigest()[:12]


def run_loop(
    *,
    next_step: Callable[[Any], dict[str, Any]],
    dispatch_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
    initial_state: Any,
    config: LoopConfig | None = None,
) -> Any:
    """Run a model+tool agent loop with safety nets.

    `next_step(state) -> {"done": bool, "tool_name"?: str, "args"?: dict, "result"?: Any, "cost_usd"?: float}`
    `dispatch_tool(name, args) -> dict` — must return a value the next_step caller can use.
    """
    cfg = config or LoopConfig()
    cfg.recent_call_window = max(cfg.repeat_call_window, cfg.repeat_call_threshold)
    state = LoopState(recent_calls=deque(maxlen=cfg.repeat_call_window))
    state.scratchpad_hash = _scratchpad_hash(initial_state)
    trace: list[dict[str, Any]] = []

    while True:
        state.turn += 1
        elapsed = time.monotonic() - state.started_at

        if state.turn > cfg.max_turns:
            raise EscalationRequired("turn cap reached", state, trace)
        if elapsed > cfg.max_wall_clock_s:
            raise EscalationRequired("wall-clock budget exhausted", state, trace)
        if state.cost_usd > cfg.max_cost_usd:
            raise EscalationRequired("cost budget exhausted", state, trace)

        step = next_step(initial_state)
        state.cost_usd += float(step.get("cost_usd", 0.0))

        if step.get("done"):
            log.info(
                "loop_done",
                extra={"turn": state.turn, "elapsed_s": round(elapsed, 2), "cost_usd": round(state.cost_usd, 4)},
            )
            return step.get("result")

        tool_name = step.get("tool_name")
        args = step.get("args", {})
        if not tool_name:
            raise EscalationRequired("model produced no tool call and did not finish", state, trace)

        call_h = _call_hash(tool_name, args)
        state.recent_calls.append(call_h)
        if state.recent_calls.count(call_h) >= cfg.repeat_call_threshold:
            raise EscalationRequired(
                f"agent repeated the same call {cfg.repeat_call_threshold}× — looks stuck",
                state,
                trace,
            )

        tool_result = dispatch_tool(tool_name, args)
        trace.append({"turn": state.turn, "tool": tool_name, "args": args, "result_summary": _short(tool_result, 120)})

        new_hash = _scratchpad_hash(initial_state)
        if new_hash == state.scratchpad_hash:
            state.scratchpad_unchanged_turns += 1
        else:
            state.scratchpad_unchanged_turns = 0
            state.scratchpad_hash = new_hash

        if state.scratchpad_unchanged_turns >= cfg.no_progress_turns:
            raise EscalationRequired(
                f"no state change for {cfg.no_progress_turns} turns",
                state,
                trace,
            )
