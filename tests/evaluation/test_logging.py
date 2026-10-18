"""Response text and expected output must reach neither DEBUG logs nor
stored evaluation details nor API responses, for every evaluator type."""

import json
import logging
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.endpoints.evaluations import get_enqueue
from components.core.schemas import Principal
from components.datasets.models import TestCase
from components.evaluation.evaluators import build_evaluator
from components.evaluation.models import EvaluationResult
from components.evaluation.schemas import parse_evaluator_config
from components.experiments.enums import ResultStatus
from components.experiments.models import ExperimentResult
from tests.access import Actors
from tests.evaluation.helpers import ScopedEvaluation
from tests.evaluation.test_repository import Factory, _setup
from tests.experiments.helpers import Enqueued

pytestmark = pytest.mark.integration

RESPONSE = "SENTINEL-RESPONSE-7d3f"
EXPECTED = "SENTINEL-EXPECTED-19ac"
ALL_EVALUATORS: dict[str, Any] = {
    "evaluators": [
        {"id": "a", "type": "exact_match"},
        {"id": "b", "type": "contains", "values": ["zz"]},
        {"id": "c", "type": "not_contains", "values": [RESPONSE[:8]]},
        {"id": "d", "type": "length_between", "min_length": 1000},
        {"id": "e", "type": "json_valid"},
        {
            "id": "f",
            "type": "json_fields",
            "required": ["k"],
            "types": {"k": "string"},
        },
    ]
}
# PostgreSQL cannot store NUL, so NUL is covered by the direct test below.
HOSTILE = [
    f"{RESPONSE} \x01 {EXPECTED}",
    '{"a":' * 100_000 + f'"{RESPONSE}"' + "}" * 100_000,
    json.dumps({"k": RESPONSE, "other": EXPECTED}),
    f"[{RESPONSE!r}",
]


@pytest.fixture(autouse=True)
def _overrides(
    app: FastAPI,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    app.dependency_overrides[get_enqueue] = lambda: Enqueued()


@pytest.fixture
async def client(actors: Actors) -> AsyncClient:
    """Authenticated as staff user A; shadows the anonymous root client."""
    return await actors.client("staff_a")


@pytest.fixture
def owner(actors: Actors) -> Principal:
    return actors.principal("staff_a")


async def _seed(
    factory: Factory, owner: Principal
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with factory() as session:
        experiment, results = await _setup(
            session,
            statuses=(ResultStatus.SUCCEEDED,) * (len(HOSTILE) + 1),
            evaluator_config=ALL_EVALUATORS,
            owner=owner,
        )
        for result, text in zip(results, [RESPONSE, *HOSTILE], strict=True):
            result.response_text = text
        # Every case gets the expected output sentinel.
        for result in results:
            case = await session.get_one(TestCase, result.test_case_id)
            case.expected_output = EXPECTED
        await session.commit()
        return experiment.id, [r.id for r in results]


async def test_sentinels_never_logged_or_stored(
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
    owner: Principal,
) -> None:
    experiment_id, ids = await _seed(session_factory, owner)
    caplog.set_level(logging.DEBUG)
    service = ScopedEvaluation(session_factory)
    for result_id in ids:
        assert await service.evaluate_result(result_id) == 6

    base = f"/api/v1/experiments/{experiment_id}"
    bodies = [
        (await client.get(f"{base}/evaluations?limit=100")).text,
        (await client.get(f"{base}/evaluations/summary")).text,
        (await client.post(f"{base}/evaluate")).text,
    ]
    async with session_factory() as session:
        rows = (await session.scalars(select(EvaluationResult))).all()
        results = (await session.scalars(select(ExperimentResult))).all()
    assert len(rows) == 6 * len(ids)
    # The sentinels really are in the stored responses (test is meaningful).
    assert any(RESPONSE in (r.response_text or "") for r in results)
    stored = json.dumps([r.details for r in rows])
    captured = caplog.text + "".join(r.getMessage() for r in caplog.records)
    for sentinel in (RESPONSE, EXPECTED, RESPONSE[:8]):
        assert sentinel not in stored
        assert sentinel not in captured
        assert all(sentinel not in body for body in bodies)
    assert {r.evaluator_type for r in rows} == {
        e["type"] for e in ALL_EVALUATORS["evaluators"]
    }
    # Hostile inputs produced outcomes rather than crashing the task.
    assert {r.status for r in rows} <= {"passed", "failed", "skipped"}


@pytest.mark.parametrize(
    "text",
    [f"{RESPONSE}\x00{EXPECTED}", f'{{"k": "{RESPONSE}\x00"}}', *HOSTILE],
)
def test_details_never_contain_text_for_any_evaluator(
    text: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    config = parse_evaluator_config(ALL_EVALUATORS)
    for entry in config.evaluators:
        outcome = build_evaluator(entry).evaluate(text, EXPECTED)
        dumped = json.dumps(outcome.details)
        for sentinel in (RESPONSE, EXPECTED, RESPONSE[:8]):
            assert sentinel not in dumped
    assert RESPONSE not in caplog.text
