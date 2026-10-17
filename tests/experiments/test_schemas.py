import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from components.experiments.schemas import (
    ExperimentCreateSchema,
    ExperimentSchema,
)


def _target(i: int = 0, provider: str = "openai") -> dict[str, str]:
    return {"provider": provider, "model": f"m{i}"}


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "exp",
        "dataset_id": str(uuid.uuid4()),
        "prompt_template": "Answer: {input}",
        "config": {"targets": [_target()]},
    }
    body.update(overrides)
    return body


def _config(**overrides: Any) -> dict[str, Any]:
    return {"targets": [_target()], **overrides}


def _loc(exc: pytest.ExceptionInfo[ValidationError]) -> tuple[Any, ...]:
    return exc.value.errors()[0]["loc"]


def test_valid_minimal_payload_gets_defaults() -> None:
    parsed = ExperimentCreateSchema.model_validate(_payload())
    assert parsed.config.max_attempts == 3
    assert parsed.config.timeout_seconds == 60.0
    assert parsed.config.temperature is None
    assert parsed.evaluator_config.model_dump() == {"evaluators": []}


def test_valid_full_payload() -> None:
    parsed = ExperimentCreateSchema.model_validate(
        _payload(
            config=_config(
                targets=[_target(i) for i in range(10)],
                system_prompt="s",
                temperature=2,
                max_tokens=1,
                timeout_seconds=5,
                max_attempts=5,
            ),
            evaluator_config={
                "evaluators": [{"id": "a", "type": "json_valid"}]
            },
        )
    )
    assert len(parsed.config.targets) == 10


def test_same_model_on_different_providers_is_distinct() -> None:
    targets = [_target(0, "openai"), _target(0, "anthropic")]
    ExperimentCreateSchema.model_validate(
        _payload(config=_config(targets=targets))
    )


@pytest.mark.parametrize(
    "template", ["no placeholder", "{expected_output} only", "{Input}", ""]
)
def test_template_without_input_placeholder(template: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(
            _payload(prompt_template=template)
        )
    assert _loc(exc) == ("prompt_template",)


def test_template_with_nul_or_too_long() -> None:
    for template in ["{input}\x00", "{input}" + "a" * 50000]:
        with pytest.raises(ValidationError) as exc:
            ExperimentCreateSchema.model_validate(
                _payload(prompt_template=template)
            )
        assert _loc(exc) == ("prompt_template",)


@pytest.mark.parametrize("name", ["", "   ", "a" * 256, "a\x00"])
def test_invalid_name(name: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(_payload(name=name))
    assert _loc(exc) == ("name",)


def test_missing_or_bad_dataset_id() -> None:
    body = _payload(dataset_id="nope")
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(body)
    assert _loc(exc) == ("dataset_id",)


def test_empty_targets() -> None:
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(
            _payload(config=_config(targets=[]))
        )
    assert _loc(exc) == ("config", "targets")


def test_more_than_ten_targets() -> None:
    targets = [_target(i) for i in range(11)]
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(
            _payload(config=_config(targets=targets))
        )
    assert _loc(exc) == ("config", "targets")


def test_duplicate_targets() -> None:
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(
            _payload(config=_config(targets=[_target(1), _target(1)]))
        )
    assert _loc(exc) == ("config",)
    assert "distinct" in str(exc.value)


def test_unknown_provider_and_blank_model() -> None:
    for target in (
        {"provider": "nope", "model": "m"},
        _target() | {"model": ""},
    ):
        with pytest.raises(ValidationError) as exc:
            ExperimentCreateSchema.model_validate(
                _payload(config=_config(targets=[target]))
            )
        assert _loc(exc)[:2] == ("config", "targets")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("max_tokens", 0),
        ("max_tokens", -1),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", 120.5),
        ("max_attempts", 0),
        ("max_attempts", 6),
        ("system_prompt", ""),
        ("system_prompt", "a\x00b"),
    ],
)
def test_invalid_generation_parameters(field: str, value: Any) -> None:
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(
            _payload(config=_config(**{field: value}))
        )
    assert _loc(exc) == ("config", field)


@pytest.mark.parametrize("value", [[], "text", 5, None])
def test_evaluator_config_must_be_object(value: Any) -> None:
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(_payload(evaluator_config=value))
    assert _loc(exc) == ("evaluator_config",)


def test_evaluator_config_error_has_field_path() -> None:
    bad = {"evaluators": [{"id": "a", "type": "contains", "values": []}]}
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(_payload(evaluator_config=bad))
    assert _loc(exc)[:3] == ("evaluator_config", "evaluators", 0)


def test_evaluator_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError) as exc:
        ExperimentCreateSchema.model_validate(
            _payload(evaluator_config={"checks": []})
        )
    assert _loc(exc) == ("evaluator_config", "checks")


def test_read_schema_parses_stored_config() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    parsed = ExperimentSchema.model_validate(
        {
            "id": uuid.uuid4(),
            "name": "e",
            "dataset_id": uuid.uuid4(),
            "status": "pending",
            "prompt_template": "{input}",
            "config": _config(max_attempts=2),
            "evaluator_config": {},
            "cancel_requested": False,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
        }
    )
    assert parsed.config.max_attempts == 2


def test_models_import_alone_resolves_foreign_keys() -> None:
    """A worker imports only experiments code; the datasets tables must
    still be known so foreign keys resolve."""
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import components.experiments.models as m;"
        "[fk.column for fk in m.Experiment.__table__.foreign_keys]"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        cwd=Path(__file__).parents[2],
    )
