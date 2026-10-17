import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from components.metrics.pricing import (
    PricingConfigError,
    RateSchema,
    estimate_cost,
    find_rate,
    load_pricing,
)

RATE = RateSchema(
    input_per_million=Decimal("1.00"), output_per_million=Decimal("2.00")
)


def _write(path: Path, data: Any) -> str:
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(path)


def _valid(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": "v1",
        "currency": "USD",
        "rates": {
            "openai": {
                "m1": {"input_per_million": 1.0, "output_per_million": 2.5}
            }
        },
    }
    data.update(overrides)
    return data


def test_cost_formula() -> None:
    assert estimate_cost(RATE, 1_000_000, 500_000) == Decimal("2.00")
    assert estimate_cost(RATE, 0, 0) == Decimal(0)


def test_cost_is_exact_decimal() -> None:
    rate = RateSchema(
        input_per_million=Decimal("0.1"), output_per_million=Decimal("0.2")
    )
    assert estimate_cost(rate, 3, 3) == Decimal("0.0000009")


@pytest.mark.parametrize("tokens", [(None, 5), (5, None), (None, None)])
def test_unknown_tokens_give_no_cost(tokens: tuple[int | None, int | None]):  # type: ignore[no-untyped-def]
    assert estimate_cost(RATE, *tokens) is None


def test_no_file_configured_is_none() -> None:
    assert load_pricing(None) is None


def test_load_and_lookup(tmp_path: Path) -> None:
    pricing = load_pricing(_write(tmp_path / "p.json", _valid()))
    assert pricing is not None
    assert pricing.version == "v1" and pricing.currency == "USD"
    rate = find_rate(pricing, "openai", "m1")
    assert rate is not None
    assert rate.output_per_million == Decimal("2.5")
    assert find_rate(pricing, "openai", "m1-2024") is None  # exact only
    assert find_rate(pricing, "anthropic", "m1") is None


def test_missing_file_names_path(tmp_path: Path) -> None:
    path = str(tmp_path / "nope.json")
    with pytest.raises(PricingConfigError) as exc:
        load_pricing(path)
    assert path in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        _valid(
            rates={
                "openai": {
                    "m": {"input_per_million": -1, "output_per_million": 1}
                }
            }
        ),
        _valid(unknown="x"),
        _valid(
            rates={
                "openai": {
                    "m": {
                        "input_per_million": 1,
                        "output_per_million": 1,
                        "extra": 1,
                    }
                }
            }
        ),
        _valid(version=""),
        _valid(
            rates={
                "openai": {
                    "m": {"input_per_million": "NaN", "output_per_million": 1}
                }
            }
        ),
        "{not json",
        "[]",
    ],
)
def test_invalid_file_raises_naming_file_not_contents(
    tmp_path: Path, data: Any
) -> None:
    path = _write(tmp_path / "p.json", data)
    with pytest.raises(PricingConfigError) as exc:
        load_pricing(path)
    assert path in str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_invalid_value_is_not_echoed(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "p.json",
        _valid(
            rates={
                "openai": {
                    "m": {
                        "input_per_million": "SECRET-VALUE",
                        "output_per_million": 1,
                    }
                }
            }
        ),
    )
    with pytest.raises(PricingConfigError) as exc:
        load_pricing(path)
    assert "SECRET-VALUE" not in str(exc.value)


def test_cache_reloads_when_file_changes(tmp_path: Path) -> None:
    path = _write(tmp_path / "p.json", _valid(version="v1"))
    first = load_pricing(path)
    assert load_pricing(path) is first  # cached object
    _write(tmp_path / "p.json", _valid(version="v2"))
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = load_pricing(path)
    assert second is not None and second.version == "v2"


def test_file_turning_invalid_raises_then_recovers(tmp_path: Path) -> None:
    path = _write(tmp_path / "p.json", _valid())
    assert load_pricing(path) is not None
    _write(tmp_path / "p.json", "{broken, and longer than before")
    with pytest.raises(PricingConfigError):
        load_pricing(path)


def test_example_file_is_valid() -> None:
    pricing = load_pricing("docs/pricing.example.json")
    assert pricing is not None
    assert "EXAMPLE" in pricing.version
