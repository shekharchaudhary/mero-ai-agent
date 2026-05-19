"""Anthropic prompt caching: correct cache_control placement and hit-rate logging.

Cache rules in short:
  - Cache breakpoints must be PREFIX-ALIGNED — every byte before the breakpoint
    must be identical across calls or the cache misses.
  - Put the most stable content first: system prompt → tools → static context →
    conversation history. User input goes LAST and is never cached.
  - Default TTL is 5 minutes; extended is 1 hour. Pick based on call cadence.

Usage:
    from prompt_cache_layout import call_with_cache, log_cache_metrics

    response = call_with_cache(
        client,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        static_context=LARGE_REFERENCE_DOC,
        history=conversation_so_far,
        user_input="What's the bug in this code?",
    )
    log_cache_metrics(response)
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 4096


def call_with_cache(
    client: Any,
    *,
    system_prompt: str,
    tools: list[dict[str, Any]] | None,
    static_context: str | None,
    history: list[dict[str, Any]],
    user_input: str,
) -> Any:
    """Build a request with cache_control on the stable layers.

    Layer order (top-down, most stable first):
      1. system_prompt           — cached
      2. tools                   — cached (via system, if you fold tool docs in)
      3. static_context          — cached (long reference material)
      4. history                 — cached up to the last stable turn
      5. user_input              — NOT cached (the variable suffix)
    """
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    if static_context:
        system_blocks.append(
            {
                "type": "text",
                "text": static_context,
                "cache_control": {"type": "ephemeral"},
            }
        )

    messages = list(history)
    if messages:
        last = messages[-1]
        if isinstance(last.get("content"), list) and last["content"]:
            last["content"][-1] = {
                **last["content"][-1],
                "cache_control": {"type": "ephemeral"},
            }
        elif isinstance(last.get("content"), str):
            last["content"] = [
                {
                    "type": "text",
                    "text": last["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]

    messages.append({"role": "user", "content": user_input})

    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system_blocks,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    return client.messages.create(**kwargs)


def log_cache_metrics(response: Any) -> dict[str, int]:
    """Pull cache metrics from a response and log hit rate.

    A healthy hot path should show >80% read vs. (read + non-cached input).
    """
    u = response.usage
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_creation = getattr(u, "cache_creation_input_tokens", 0) or 0
    input_tokens = getattr(u, "input_tokens", 0) or 0
    output_tokens = getattr(u, "output_tokens", 0) or 0

    cached_eligible = cache_read + cache_creation
    total_input = input_tokens + cached_eligible
    hit_rate = cache_read / total_input if total_input else 0.0

    metrics = {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
        "output_tokens": output_tokens,
        "cache_hit_rate": round(hit_rate, 3),
    }
    log.info("prompt_cache_metrics", extra=metrics)
    return metrics
