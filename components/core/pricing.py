"""Operator-supplied pricing and the pure cost estimate.

No rates are shipped as facts: costs exist only when the operator points
``PRICING_FILE`` at a file. The file is read lazily and cached per process
keyed by its mtime, so edits apply without a restart. Errors name the file
path and the reason, never the file contents.
"""

import json
import os
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from components.core.exceptions import PricingConfigError

TOKENS_PER_UNIT = Decimal(1_000_000)


class RateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)


class PricingFileSchema(BaseModel):
    """``rates`` is keyed by provider, then by the requested model id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    rates: dict[str, dict[str, RateSchema]]


_cache: dict[str, tuple[int, int, PricingFileSchema]] = {}


def _describe(exc: ValidationError) -> str:
    # Only locations and messages: error inputs would echo file contents.
    parts = [
        ".".join(str(p) for p in err["loc"]) + ": " + err["msg"]
        for err in exc.errors(include_input=False, include_url=False)
    ]
    return "; ".join(parts)


def load_pricing(path: str | None) -> PricingFileSchema | None:
    """Return the validated pricing, or ``None`` when no file is configured.

    Raises ``PricingConfigError`` when the file cannot be read or is
    invalid. Failures are not cached.
    """
    if path is None:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        raise PricingConfigError(path, "file not found or unreadable") from None
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _cache.get(path)
    if cached is not None and cached[:2] == key:
        return cached[2]
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        raise PricingConfigError(path, "file not found or unreadable") from None
    try:
        # Parsing from JSON text keeps numbers exact (no float round trip).
        pricing = PricingFileSchema.model_validate_json(raw)
    except ValidationError as exc:
        reason = (
            "not valid JSON"
            if any(e["type"] == "json_invalid" for e in exc.errors())
            else _describe(exc)
        )
        raise PricingConfigError(path, reason) from None
    except (json.JSONDecodeError, ValueError):
        raise PricingConfigError(path, "not valid JSON") from None
    _cache[path] = (*key, pricing)
    return pricing


def find_rate(
    pricing: PricingFileSchema, provider: str, model: str
) -> RateSchema | None:
    """Exact match on provider and requested model id; no fuzzy matching."""
    return pricing.rates.get(provider, {}).get(model)


def estimate_cost(
    rate: RateSchema, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    """Estimated cost in the pricing currency, or ``None`` when either token
    count is unknown (an unknown count is never treated as zero)."""
    if input_tokens is None or output_tokens is None:
        return None
    return (
        Decimal(input_tokens) * rate.input_per_million / TOKENS_PER_UNIT
        + Decimal(output_tokens) * rate.output_per_million / TOKENS_PER_UNIT
    )
