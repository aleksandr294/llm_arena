"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.endpoints.evaluations import get_enqueue, get_factory
from components.evaluation import service as service_module
from components.evaluation.repository import EvaluationRepository
from components.evaluation.service import EvaluationService
from components.experiments.models import ResultStatus
from tests.evaluation.test_repository import Factory, _setup
from tests.experiments.helpers import Enqueued

pytestmark = pytest.mark.integration

CONFIG: dict[str, Any] = {
    "evaluators": [
        {"id": "exact", "type": "exact_match"},
        {"id": "has_r", "type": "contains", "values": ["r"]},
    ]
}


@pytest.fixture
def enqueued() -> Enqueued:
    return Enqueued()


@pytest.fixture(autouse=True)
def _overrides(
    app: FastAPI,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    enqueued: Enqueued,
) -> None:
    app.dependency_overrides[get_factory] = lambda: session_factory
    app.dependency_overrides[get_enqueue] = lambda: enqueued


async def _make(
    factory: Factory, config: dict[str, Any] | None = CONFIG
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with factory() as session:
        experiment, results = await _setup(session, evaluator_config=config)
        await session.commit()
        return experiment.id, [r.id for r in results]


def url(experiment_id: uuid.UUID, suffix: str) -> str:
    return f"/api/v1/experiments/{experiment_id}/{suffix}"


async def test_evaluate_enqueues_unevaluated_succeeded_results(
    client: AsyncClient, session_factory: Factory, enqueued: Enqueued
) -> None:
    experiment_id, ids = await _make(session_factory)
    response = await client.post(url(experiment_id, "evaluate"))
    assert response.status_code == 202
    assert response.json() == {
        "experiment_id": str(experiment_id),
        "enqueued": 2,
        "remaining": 0,
    }
    assert enqueued.flat == ids[:2]  # the failed generation is not included

    # Evaluate one; only the other remains.
    await EvaluationService(session_factory).evaluate_result(ids[0])
    enqueued.batches.clear()
    body = (await client.post(url(experiment_id, "evaluate"))).json()
    assert body["enqueued"] == 1
    assert enqueued.flat == [ids[1]]


async def test_evaluate_with_nothing_to_do_returns_zero(
    client: AsyncClient, session_factory: Factory, enqueued: Enqueued
) -> None:
    experiment_id, ids = await _make(session_factory)
    service = EvaluationService(session_factory)
    for result_id in ids:
        await service.evaluate_result(result_id)
    response = await client.post(url(experiment_id, "evaluate"))
    assert response.status_code == 202
    assert response.json()["enqueued"] == 0
    assert response.json()["remaining"] == 0
    assert enqueued.batches == []


async def test_evaluate_enqueues_a_bounded_batch(
    client: AsyncClient,
    session_factory: Factory,
    enqueued: Enqueued,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "BACKFILL_BATCH_SIZE", 1)
    experiment_id, ids = await _make(session_factory)
    body = (await client.post(url(experiment_id, "evaluate"))).json()
    assert (body["enqueued"], body["remaining"]) == (1, 1)
    assert enqueued.flat == [ids[0]]


@pytest.mark.parametrize("config", [None, {}, {"evaluators": []}])
async def test_evaluate_without_evaluators_returns_zero(
    client: AsyncClient,
    session_factory: Factory,
    enqueued: Enqueued,
    config: dict[str, Any] | None,
) -> None:
    experiment_id, _ = await _make(session_factory, config)
    response = await client.post(url(experiment_id, "evaluate"))
    assert response.status_code == 202
    assert response.json()["enqueued"] == 0
    assert enqueued.batches == []


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("POST", "evaluate"),
        ("GET", "evaluations"),
        ("GET", "evaluations/summary"),
    ],
)
async def test_unknown_experiment_is_404(
    client: AsyncClient, method: str, suffix: str
) -> None:
    response = await client.request(method, url(uuid.uuid4(), suffix))
    assert response.status_code == 404


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=x",
        "offset=-1",
        "status=bogus",
        "status=PASSED",
    ],
)
async def test_list_invalid_query_is_422(
    client: AsyncClient, session_factory: Factory, query: str
) -> None:
    experiment_id, _ = await _make(session_factory)
    response = await client.get(url(experiment_id, f"evaluations?{query}"))
    assert response.status_code == 422


async def test_invalid_experiment_id_is_422(client: AsyncClient) -> None:
    for suffix in ("evaluate", "evaluations", "evaluations/summary"):
        method = "POST" if suffix == "evaluate" else "GET"
        response = await client.request(
            method, f"/api/v1/experiments/not-a-uuid/{suffix}"
        )
        assert response.status_code == 422


async def _evaluate_all(factory: Factory, ids: list[uuid.UUID]) -> None:
    service = EvaluationService(factory)
    for result_id in ids:
        await service.evaluate_result(result_id)


async def test_list_pagination_filters_and_shape(
    client: AsyncClient, session_factory: Factory
) -> None:
    experiment_id, ids = await _make(session_factory)
    await _evaluate_all(session_factory, ids)

    body = (await client.get(url(experiment_id, "evaluations"))).json()
    assert (body["total"], body["limit"], body["offset"]) == (4, 50, 0)
    assert [(i["evaluator_id"], i["status"]) for i in body["items"]] == [
        ("exact", "skipped"),
        ("has_r", "passed"),
        ("exact", "failed"),
        ("has_r", "passed"),
    ]
    item = body["items"][0]
    assert set(item) == {
        "id",
        "experiment_result_id",
        "evaluator_id",
        "evaluator_type",
        "evaluator_version",
        "status",
        "details",
        "created_at",
    }
    assert item["experiment_result_id"] == str(ids[0])
    assert item["details"] == {"reason": "no_expected_output"}

    page = (
        await client.get(url(experiment_id, "evaluations?limit=2&offset=3"))
    ).json()
    assert (page["total"], page["limit"], page["offset"]) == (4, 2, 3)
    assert len(page["items"]) == 1

    by_eval = (
        await client.get(url(experiment_id, "evaluations?evaluator_id=exact"))
    ).json()
    assert by_eval["total"] == 2
    assert {i["evaluator_id"] for i in by_eval["items"]} == {"exact"}

    by_status = (
        await client.get(url(experiment_id, "evaluations?status=passed"))
    ).json()
    assert by_status["total"] == 2
    both = (
        await client.get(
            url(experiment_id, "evaluations?evaluator_id=exact&status=failed")
        )
    ).json()
    assert both["total"] == 1
    none = (
        await client.get(url(experiment_id, "evaluations?evaluator_id=zzz"))
    ).json()
    assert none == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_summary_counts_per_outcome_and_unevaluated(
    client: AsyncClient, session_factory: Factory
) -> None:
    experiment_id, ids = await _make(session_factory)

    before = (
        await client.get(url(experiment_id, "evaluations/summary"))
    ).json()
    assert before["unevaluated_results"] == 2
    assert before["evaluators"][0] == {
        "id": "exact",
        "type": "exact_match",
        "version": 1,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "error": 0,
    }

    await EvaluationService(session_factory).evaluate_result(ids[0])
    await EvaluationService(session_factory).evaluate_result(ids[1])
    summary = (
        await client.get(url(experiment_id, "evaluations/summary"))
    ).json()
    assert summary["unevaluated_results"] == 0
    exact, has_r = summary["evaluators"]
    assert (exact["passed"], exact["failed"], exact["skipped"]) == (0, 1, 1)
    assert (has_r["passed"], has_r["failed"]) == (2, 0)
    assert exact["error"] == has_r["error"] == 0


async def test_summary_without_evaluators_is_empty(
    client: AsyncClient, session_factory: Factory
) -> None:
    experiment_id, _ = await _make(session_factory, {})
    body = (await client.get(url(experiment_id, "evaluations/summary"))).json()
    assert body == {"evaluators": [], "unevaluated_results": 0}


async def test_summary_counts_error_outcomes(
    client: AsyncClient, session_factory: Factory
) -> None:
    experiment_id, ids = await _make(session_factory)
    async with session_factory() as session:
        await EvaluationRepository(session).insert_ignore(
            [
                {
                    "experiment_result_id": ids[1],
                    "experiment_id": experiment_id,
                    "evaluator_id": "exact",
                    "evaluator_type": "exact_match",
                    "evaluator_version": 1,
                    "status": "error",
                    "details": {"reason": "evaluator_exception"},
                }
            ]
        )
        await session.commit()
    body = (await client.get(url(experiment_id, "evaluations/summary"))).json()
    assert body["evaluators"][0]["error"] == 1
    assert body["unevaluated_results"] == 2  # has_r is still missing


async def test_failed_results_never_count_as_unevaluated(
    client: AsyncClient, session_factory: Factory
) -> None:
    async with session_factory() as session:
        experiment, _ = await _setup(
            session,
            statuses=(ResultStatus.FAILED, ResultStatus.CANCELLED),
            evaluator_config=CONFIG,
        )
        await session.commit()
        experiment_id = experiment.id
    body = (await client.get(url(experiment_id, "evaluations/summary"))).json()
    assert body["unevaluated_results"] == 0
