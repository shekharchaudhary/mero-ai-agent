"""Reference tool definition: a well-designed `send_email` tool.

Demonstrates the patterns from SKILL.md:
  - imperative verb-noun name
  - typed inputs with enums and explicit defaults
  - dry_run default for irreversible writes; explicit confirm required
  - idempotency key for safe retries
  - consistent envelope on success and error
  - typed error codes with retryable flag
  - description tells the model when NOT to use this tool

The schema below is the shape Anthropic SDK expects in `tools=[...]`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

# In-memory idempotency cache; replace with a durable store in prod.
_IDEMPOTENCY_CACHE: dict[str, dict[str, Any]] = {}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


SEND_EMAIL_TOOL = {
    "name": "send_email",
    "description": (
        "Send a single transactional email from a verified sender to one recipient. "
        "Use draft_email for messages that should be reviewed before sending. "
        "Use send_bulk_email for >1 recipient — this tool refuses multiple recipients."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address. Exactly one address; no CC/BCC.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject. 1-200 chars.",
            },
            "body": {
                "type": "string",
                "description": "Plain-text body. Markdown is not rendered.",
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "high"],
                "description": "Delivery priority. Default 'normal'.",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "If true (default), validate inputs and return what WOULD be sent "
                    "without sending. Set to false only when the user has confirmed."
                ),
            },
            "confirm": {
                "type": "boolean",
                "description": (
                    "Must be true to actually send when dry_run is false. "
                    "A safety interlock — both dry_run=false AND confirm=true are required to send."
                ),
            },
            "idempotency_key": {
                "type": "string",
                "description": (
                    "Client-supplied key. Retrying with the same key returns the original "
                    "result rather than sending again. Required when dry_run is false."
                ),
            },
        },
        "required": ["to", "subject", "body"],
    },
}


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] | None = None
    error_code: Literal[
        "invalid_argument", "permission_denied", "rate_limited",
        "conflict", "internal_error", "not_found",
    ] | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
            "error": (
                None if self.ok
                else {"code": self.error_code, "retryable": self.retryable}
            ),
        }


def _err(code: str, msg: str, retryable: bool = False) -> dict[str, Any]:
    return ToolResult(
        ok=False, summary=msg, error_code=code, retryable=retryable  # type: ignore[arg-type]
    ).to_dict()


def send_email(
    to: str,
    subject: str,
    body: str,
    priority: str = "normal",
    dry_run: bool = True,
    confirm: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Implementation matching SEND_EMAIL_TOOL schema.

    Always returns the envelope shape `{ok, summary, data, error}`.
    """
    if not EMAIL_RE.match(to):
        return _err("invalid_argument", f"'{to}' is not a valid email address.")
    if "," in to or ";" in to:
        return _err(
            "invalid_argument",
            "send_email accepts exactly one recipient. Use send_bulk_email for multiple.",
        )
    if not 1 <= len(subject) <= 200:
        return _err("invalid_argument", "subject must be 1-200 chars.")
    if not body.strip():
        return _err("invalid_argument", "body must not be empty.")
    if priority not in ("normal", "high"):
        return _err("invalid_argument", "priority must be 'normal' or 'high'.")

    if dry_run:
        return ToolResult(
            ok=True,
            summary=f"DRY RUN: would send to {to} with subject {subject!r}.",
            data={"to": to, "subject": subject, "priority": priority, "would_send": True},
        ).to_dict()

    if not confirm:
        return _err(
            "invalid_argument",
            "Refusing to send: dry_run=false requires confirm=true. "
            "Ask the user to confirm before retrying.",
        )

    if not idempotency_key:
        return _err(
            "invalid_argument",
            "idempotency_key is required when dry_run=false. "
            "Generate a stable key derived from the user's request.",
        )

    cached = _IDEMPOTENCY_CACHE.get(idempotency_key)
    if cached is not None:
        return cached

    try:
        message_id = hashlib.sha1(
            f"{idempotency_key}|{to}|{subject}".encode()
        ).hexdigest()[:16]
        # ... actual provider call would go here ...
        result = ToolResult(
            ok=True,
            summary=f"Sent to {to}.",
            data={"message_id": message_id, "to": to, "subject": subject},
        ).to_dict()
    except TimeoutError:
        return _err("rate_limited", "Provider timed out. Retry with the same idempotency_key.", retryable=True)
    except Exception as e:
        return _err("internal_error", f"Provider error: {type(e).__name__}.")

    _IDEMPOTENCY_CACHE[idempotency_key] = result
    return result
