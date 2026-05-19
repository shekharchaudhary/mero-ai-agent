"""Versioned pricing tables for Anthropic models.

IMPORTANT: The prices below are an example snapshot. Before deploying:
  1. Verify every rate against the current Anthropic pricing page.
  2. Update PRICE_VERSION to a new date string.
  3. Keep older PriceTable entries in PRICE_HISTORY so historical ledger
     entries that reference old versions can still be priced correctly.

All rates are USD per 1,000,000 tokens unless otherwise noted.

Usage:
    from pricing import cost_usd_for, Usage, PRICE_VERSION

    usage = Usage(
        model_id="claude-opus-4-7",
        input_tokens=2_000,
        output_tokens=500,
        cache_creation_tokens=10_000,
        cache_read_tokens=8_000,
    )
    cost, version = cost_usd_for(usage)
    assert version == PRICE_VERSION
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token prices for one model."""
    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


@dataclass(frozen=True)
class PriceTable:
    version: str
    rates: dict[str, ModelPrice]


# ---------------------------------------------------------------------------
# EXAMPLE SNAPSHOT. Verify these numbers against the current Anthropic pricing
# page before relying on them. The shape is correct; the values may not be.
# ---------------------------------------------------------------------------

PRICE_TABLE_2026_05 = PriceTable(
    version="2026-05",
    rates={
        # Numbers below are placeholders modeled on Anthropic's pricing shape.
        # Replace with the verified current values.
        "claude-opus-4-7": ModelPrice(
            input=15.00, output=75.00,
            cache_write_5m=18.75, cache_write_1h=30.00, cache_read=1.50,
        ),
        "claude-sonnet-4-6": ModelPrice(
            input=3.00, output=15.00,
            cache_write_5m=3.75, cache_write_1h=6.00, cache_read=0.30,
        ),
        "claude-haiku-4-5-20251001": ModelPrice(
            input=0.80, output=4.00,
            cache_write_5m=1.00, cache_write_1h=1.60, cache_read=0.08,
        ),
    },
)

PRICE_TABLE_CURRENT = PRICE_TABLE_2026_05
PRICE_VERSION = PRICE_TABLE_CURRENT.version

# Keep old tables here so re-pricing a historical entry by its price_version
# remains possible after a rate change.
PRICE_HISTORY: dict[str, PriceTable] = {
    PRICE_TABLE_2026_05.version: PRICE_TABLE_2026_05,
}


@dataclass(frozen=True)
class Usage:
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cache_ttl: str = "5m"   # "5m" | "1h"


def cost_usd_for(
    usage: Usage,
    table: PriceTable = PRICE_TABLE_CURRENT,
) -> tuple[float, str]:
    """Return (cost_usd, price_version). Raises KeyError if model not priced."""
    if usage.model_id not in table.rates:
        raise KeyError(f"No price for model_id={usage.model_id!r} in version {table.version}")
    p = table.rates[usage.model_id]
    write_rate = p.cache_write_1h if usage.cache_ttl == "1h" else p.cache_write_5m
    per_mil = (
        usage.input_tokens          * p.input
        + usage.output_tokens       * p.output
        + usage.cache_creation_tokens * write_rate
        + usage.cache_read_tokens   * p.cache_read
    )
    return round(per_mil / 1_000_000, 6), table.version


def cost_usd_for_historical(usage: Usage, price_version: str) -> float:
    """Re-price a ledger entry using the table that was current when it was written."""
    table = PRICE_HISTORY.get(price_version)
    if table is None:
        raise KeyError(f"Unknown price_version: {price_version!r}")
    cost, _ = cost_usd_for(usage, table=table)
    return cost
