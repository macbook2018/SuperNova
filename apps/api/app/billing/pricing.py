"""Deterministic quote primitives, NOT an active charging service.

Inputs are immutable price-version snapshots. No remote provider charges, wallet
updates, reservation actions or DB writes happen in this module.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class ModelUsageQuote:
    model_source: str
    input_tokens: int
    output_tokens: int
    amount_minor_exact: Decimal


@dataclass(frozen=True)
class ResourceOverageQuote:
    previous_units: int
    requested_units: int
    included_units: int
    overage_units: int
    amount_minor_exact: Decimal


def _valid_rate(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("price must be a finite non-negative Decimal")
    return value


def _valid_units(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def quote_model_tokens(
    *,
    model_source: str,
    input_tokens: int,
    output_tokens: int,
    input_per_million_minor: Decimal,
    output_per_million_minor: Decimal,
) -> ModelUsageQuote:
    """Return exact platform-model price in minor currency units.

    BYOK always has ZERO SuperNova model charges even if the tokens were metered.
    Platform usage must be proven upstream; callers cannot use an estimated
    output quantity as an actual settlement result.
    """
    if model_source not in {"platform", "byok"}:
        raise ValueError("model_source must be 'platform' or 'byok'")
    _valid_units(input_tokens, "input_tokens")
    _valid_units(output_tokens, "output_tokens")
    input_rate = _valid_rate(input_per_million_minor)
    output_rate = _valid_rate(output_per_million_minor)

    if model_source == "byok":
        amount = Decimal(0)
    else:
        denominator = Decimal(1_000_000)
        amount = (
            Decimal(input_tokens) * input_rate / denominator
            + Decimal(output_tokens) * output_rate / denominator
        )
    return ModelUsageQuote(model_source, input_tokens, output_tokens, amount)


def quote_resource_overage(
    *,
    previous_units: int,
    requested_units: int,
    included_units: int,
    overage_rate_minor_per_unit: Decimal,
) -> ResourceOverageQuote:
    """Bill only newly crossing or newly exceeding a fixed period allowance."""
    previous = _valid_units(previous_units, "previous_units")
    requested = _valid_units(requested_units, "requested_units")
    included = _valid_units(included_units, "included_units")
    rate = _valid_rate(overage_rate_minor_per_unit)
    already_excess = max(0, previous - included)
    total_excess = max(0, previous + requested - included)
    excess = total_excess - already_excess
    return ResourceOverageQuote(
        previous, requested, included, excess, Decimal(excess) * rate
    )


def round_invoice_minor(amounts: list[Decimal]) -> int:
    """Round once at the charge/invoice boundary, never for every token chunk."""
    if not all(isinstance(amount, Decimal) and amount.is_finite() and amount >= 0
               for amount in amounts):
        raise ValueError("invoice amounts must be finite non-negative Decimals")
    return int(sum(amounts, Decimal(0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def validate_balanced_postings(amounts_minor: list[int]) -> None:
    """Validate one signed journal; caller must enforce currency and DB locks."""
    if len(amounts_minor) < 2:
        raise ValueError("a journal requires at least two postings")
    if any(
        isinstance(amount, bool) or not isinstance(amount, int) or amount == 0
        for amount in amounts_minor
    ):
        raise ValueError("posting amounts must be non-zero integers in minor units")
    if sum(amounts_minor) != 0:
        raise ValueError("journal postings must sum to zero")
