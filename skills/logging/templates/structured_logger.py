"""Minimal structured logger for an AI agent.

Usage:
    from structured_logger import get_logger, new_run

    log = get_logger()
    with new_run(user_prompt="...") as trace_id:
        log.info("tool_call", tool_name="Read", args={"path": "/tmp/x"})
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)

REDACT_KEYS = {
    "api_key", "authorization", "password", "secret", "token",
    "cookie", "set-cookie", "x-api-key",
}

REDACT_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),          # OpenAI / Anthropic style keys
    re.compile(r"AKIA[0-9A-Z]{16}"),             # AWS access key
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\."),     # JWT prefix
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
]

MAX_STR_LEN = 200


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        for pat in REDACT_PATTERNS:
            if pat.search(v):
                return "[REDACTED]"
        if len(v) > MAX_STR_LEN:
            return f"{v[:MAX_STR_LEN]}…[truncated {len(v) - MAX_STR_LEN} chars]"
        return v
    if isinstance(v, dict):
        return _redact_dict(v)
    if isinstance(v, list):
        return [_redact_value(x) for x in v]
    return v


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if k.lower() in REDACT_KEYS:
            out[k] = "[REDACTED]"
        else:
            out[k] = _redact_value(v)
    return out


def _redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return _redact_dict(event_dict)


def _trace_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    tid = _trace_id.get()
    if tid:
        event_dict.setdefault("trace_id", tid)
    return event_dict


def configure(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _trace_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )


def get_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()


@contextmanager
def new_run(**start_fields: Any):
    """Start a new agent run with a fresh trace_id."""
    tid = str(uuid.uuid4())
    token = _trace_id.set(tid)
    log = get_logger()
    log.info("run_start", **start_fields)
    try:
        yield tid
        log.info("run_complete", outcome="ok")
    except Exception as e:
        log.error("run_complete", outcome="error", error_type=type(e).__name__, error_msg=str(e))
        raise
    finally:
        _trace_id.reset(token)


configure()
