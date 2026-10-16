"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.endpoints.experiments import get_enqueue, get_llm_service
from components.core.database import get_session
from components.experiments.runner import ExperimentRunner
from components.third_party.llm.schemas import LLMErrorKind, ProviderName
from tests.experiments.helpers import (
    Enqueued,
    Factory,
    FakeLLM,
    make_dataset,
    provider_error,
)

pytestmark = pytest.mark.integration

URL = "/api/v1/experiments"


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def enqueued() -> Enqueued:
    return Enqueued()


@pytest.fixture(autouse=True)
def _overrides(
    app: FastAPI,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    llm: FakeLLM,
    enqueued: Enqueued,
) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        # Same unit-of-work semantics as get_session.
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_llm_service] = llm.as_service
    app.dependency_overrides[get_enqueue] = lambda: enqueued


def payload(dataset_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "exp",
        "dataset_id": str(dataset_id),
        "prompt_template": "Q: {input}",
        "config": {"targets": [{"provider": "openai", "model": "m1"}]},
    }
    body.update(overrides)
    return body


async def _create(client: AsyncClient, factory: Factory, cases: int = 3) -> str:
    dataset_id = await make_dataset(factory, cases)
    response = await client.post(URL, json=payload(dataset_id))
    assert response.status_code == 201
    return str(response.json()["id"])


async def _run_all(factory: Factory, llm: FakeLLM, enqueued: Enqueued) -> None:
    runner = ExperimentRunner(factory, llm.as_service(), 4, jitter=lambda: 0.0)
    for result_id in enqueued.flat:
        await runner.run(result_id)
    enqueued.batches.clear()


async def test_create_returns_201_and_persists_pending(
    client: AsyncClient, session_factory: Factory, enqueued: Enqueued
) -> None:
    dataset_id = await make_dataset(session_factory, 2)
    response = await client.post(URL, json=payload(dataset_id))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["config"]["max_attempts"] == 3
    assert body["evaluator_config"] == {"evaluators": []}
    assert enqueued.flat == []  # creating never enqueues

    got = (await client.get(f"{URL}/{body['id']}")).json()
    assert got["status_counts"] == {"pending": 2}
    results = (await client.get(f"{URL}/{body['id']}/results")).json()
    assert results["total"] == 2
    assert results["items"][0]["rendered_prompt"] == "Q: in1"
    assert results["items"][0]["response_text"] is None


async def test_list_experiments_paginated(
    client: AsyncClient, session_factory: Factory
) -> None:
    ids = [await _create(client, session_factory, 1) for _ in range(3)]
    body = (await client.get(f"{URL}?limit=2&offset=2")).json()
    assert body["total"] == 3
    assert (body["limit"], body["offset"]) == (2, 2)
    assert [e["id"] for e in body["items"]] == ids[2:]


async def test_start_returns_202_and_enqueues(
    client: AsyncClient, session_factory: Factory, enqueued: Enqueued
) -> None:
    experiment_id = await _create(client, session_factory)
    response = await client.post(f"{URL}/{experiment_id}/start")
    assert response.status_code == 202
    body = response.json()
    assert body["enqueued"] == 3
    assert body["experiment"]["status"] == "running"
    assert len(enqueued.flat) == 3
    # Submission is not execution.
    got = (await client.get(f"{URL}/{experiment_id}")).json()
    assert got["status_counts"] == {"pending": 3}


async def test_status_counts_and_results_filter(
    client: AsyncClient,
    session_factory: Factory,
    llm: FakeLLM,
    enqueued: Enqueued,
) -> None:
    experiment_id = await _create(client, session_factory)
    llm.script = [
        provider_error(LLMErrorKind.AUTHENTICATION),
    ]
    await client.post(f"{URL}/{experiment_id}/start")
    await _run_all(session_factory, llm, enqueued)

    got = (await client.get(f"{URL}/{experiment_id}")).json()
    assert got["status"] == "partially_failed"
    assert got["status_counts"] == {"failed": 1, "succeeded": 2}

    failed = (
        await client.get(f"{URL}/{experiment_id}/results?status=failed")
    ).json()
    assert failed["total"] == 1
    assert failed["items"][0]["error_kind"] == "authentication"
    ok = (
        await client.get(
            f"{URL}/{experiment_id}/results?status=succeeded&limit=1"
        )
    ).json()
    assert ok["total"] == 2
    assert len(ok["items"]) == 1
    assert ok["items"][0]["response_text"] == "ok"


async def test_retry_failed_returns_202_only_failed(
    client: AsyncClient,
    session_factory: Factory,
    llm: FakeLLM,
    enqueued: Enqueued,
) -> None:
    experiment_id = await _create(client, session_factory)
    llm.script = [provider_error(LLMErrorKind.AUTHENTICATION)]
    await client.post(f"{URL}/{experiment_id}/start")
    await _run_all(session_factory, llm, enqueued)
    calls = len(llm.calls)

    response = await client.post(f"{URL}/{experiment_id}/retry-failed")
    assert response.status_code == 202
    assert response.json()["enqueued"] == 1
    assert response.json()["experiment"]["status"] == "running"
    await _run_all(session_factory, llm, enqueued)
    assert len(llm.calls) == calls + 1
    got = (await client.get(f"{URL}/{experiment_id}")).json()
    assert got["status"] == "completed"
    assert got["status_counts"] == {"succeeded": 3}


async def test_cancel_pending_experiment(
    client: AsyncClient, session_factory: Factory
) -> None:
    experiment_id = await _create(client, session_factory)
    response = await client.post(f"{URL}/{experiment_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    got = (await client.get(f"{URL}/{experiment_id}")).json()
    assert got["status_counts"] == {"cancelled": 3}


async def test_unknown_experiment_is_404(client: AsyncClient) -> None:
    missing = uuid.uuid4()
    for response in (
        await client.get(f"{URL}/{missing}"),
        await client.get(f"{URL}/{missing}/results"),
        await client.post(f"{URL}/{missing}/start"),
        await client.post(f"{URL}/{missing}/cancel"),
        await client.post(f"{URL}/{missing}/retry-failed"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Experiment not found"}


async def test_unknown_dataset_is_404(client: AsyncClient) -> None:
    response = await client.post(URL, json=payload(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


async def test_invalid_transitions_are_409(
    client: AsyncClient,
    session_factory: Factory,
    llm: FakeLLM,
    enqueued: Enqueued,
) -> None:
    # Retry a pending experiment.
    experiment_id = await _create(client, session_factory)
    assert (
        await client.post(f"{URL}/{experiment_id}/retry-failed")
    ).status_code == 409

    # Everything on a completed experiment.
    await client.post(f"{URL}/{experiment_id}/start")
    await _run_all(session_factory, llm, enqueued)
    for action in ("start", "cancel", "retry-failed"):
        response = await client.post(f"{URL}/{experiment_id}/{action}")
        assert response.status_code == 409, action
        assert "completed" in response.json()["detail"]

    # Start and retry a cancelled experiment.
    cancelled = await _create(client, session_factory)
    await client.post(f"{URL}/{cancelled}/cancel")
    for action in ("start", "cancel", "retry-failed"):
        response = await client.post(f"{URL}/{cancelled}/{action}")
        assert response.status_code == 409, action


async def test_start_twice_while_running_is_allowed(
    client: AsyncClient, session_factory: Factory, enqueued: Enqueued
) -> None:
    experiment_id = await _create(client, session_factory, 2)
    await client.post(f"{URL}/{experiment_id}/start")
    again = await client.post(f"{URL}/{experiment_id}/start")
    assert again.status_code == 202
    assert again.json()["enqueued"] == 2


async def _loc(client: AsyncClient, body: dict[str, Any]) -> list[Any]:
    response = await client.post(URL, json=body)
    assert response.status_code == 422
    return list(response.json()["detail"][0]["loc"])


async def test_create_422_cases(
    client: AsyncClient, session_factory: Factory, llm: FakeLLM
) -> None:
    dataset_id = await make_dataset(session_factory, 2)
    target = {"provider": "openai", "model": "m1"}

    assert await _loc(client, payload(dataset_id, config={"targets": []})) == [
        "body",
        "config",
        "targets",
    ]
    assert await _loc(
        client, payload(dataset_id, config={"targets": [target, target]})
    ) == ["body", "config"]
    assert await _loc(
        client, payload(dataset_id, prompt_template="no placeholder")
    ) == ["body", "prompt_template"]
    assert await _loc(
        client,
        payload(
            dataset_id,
            evaluator_config={
                "evaluators": [
                    {"id": "a", "type": "length_between", "min_length": -1}
                ]
            },
        ),
    ) == [
        "body",
        "evaluator_config",
        "evaluators",
        0,
        "length_between",
        "min_length",
    ]
    assert await _loc(client, payload(dataset_id, name="  ")) == [
        "body",
        "name",
    ]

    llm.unconfigured = {ProviderName.OPENAI}
    response = await client.post(URL, json=payload(dataset_id))
    assert response.status_code == 422
    detail = response.json()["detail"][0]
    assert detail["loc"] == ["body", "config", "targets"]
    assert "OPENAI_API_KEY" in detail["msg"]

    llm.unconfigured = set()
    empty = await make_dataset(session_factory, 0)
    assert await _loc(client, payload(empty)) == ["body", "dataset_id"]

    # Nothing was created by any rejected request.
    assert (await client.get(URL)).json()["total"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "?limit=0",
        "?limit=101",
        "?offset=-1",
    ],
)
async def test_bad_pagination_422(
    client: AsyncClient, session_factory: Factory, path: str
) -> None:
    experiment_id = await _create(client, session_factory, 1)
    assert (await client.get(f"{URL}{path}")).status_code == 422
    response = await client.get(f"{URL}/{experiment_id}/results{path}")
    assert response.status_code == 422


async def test_invalid_status_filter_and_id_422(
    client: AsyncClient, session_factory: Factory
) -> None:
    experiment_id = await _create(client, session_factory, 1)
    response = await client.get(f"{URL}/{experiment_id}/results?status=bogus")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "status"]
    assert (await client.get(f"{URL}/not-a-uuid")).status_code == 422


async def test_valid_evaluator_config_is_stored_and_returned(
    client: AsyncClient, session_factory: Factory
) -> None:
    dataset_id = await make_dataset(session_factory, 1)
    config = {
        "evaluators": [
            {"id": "em", "type": "exact_match"},
            {"id": "js", "type": "json_valid"},
        ]
    }
    response = await client.post(
        URL, json=payload(dataset_id, evaluator_config=config)
    )
    assert response.status_code == 201
    stored = response.json()["evaluator_config"]["evaluators"]
    assert [(e["id"], e["type"]) for e in stored] == [
        ("em", "exact_match"),
        ("js", "json_valid"),
    ]
    got = (await client.get(f"{URL}/{response.json()['id']}")).json()
    assert got["evaluator_config"] == response.json()["evaluator_config"]


@pytest.mark.parametrize(
    "evaluator_config",
    [
        {"evaluators": [{"id": "a", "type": "no_such_type"}]},
        {
            "evaluators": [
                {"id": "a", "type": "exact_match"},
                {"id": "a", "type": "json_valid"},
            ]
        },
        ["not", "an", "object"],
    ],
)
async def test_invalid_evaluator_config_is_422(
    client: AsyncClient, session_factory: Factory, evaluator_config: Any
) -> None:
    dataset_id = await make_dataset(session_factory, 1)
    loc = await _loc(
        client, payload(dataset_id, evaluator_config=evaluator_config)
    )
    assert loc[:2] == ["body", "evaluator_config"]
    assert (await client.get(URL)).json()["total"] == 0
