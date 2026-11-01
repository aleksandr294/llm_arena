"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.endpoints.experiments import get_enqueue, get_llm_service
from components.core.database import get_session, session_scope
from components.core.schemas import Principal
from components.experiments.models import Experiment, ExperimentResult
from components.experiments.runner import ExperimentRunner
from components.third_party.llm.enums import LLMErrorKind, ProviderName
from tests.access import Actors
from tests.experiments.helpers import (
    Enqueued,
    Factory,
    FakeLLM,
    make_dataset,
    provider_error,
    scope_of,
)

pytestmark = pytest.mark.integration

URL = "/api/v1/experiments"


@pytest.fixture
async def client(actors: Actors) -> AsyncClient:
    """Authenticated as staff user A; shadows the anonymous root client."""
    return await actors.client("staff_a")


@pytest.fixture
def owner(actors: Actors) -> Principal:
    return actors.principal("staff_a")


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
        async with session_scope(session_factory) as session:
            yield session

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


async def _create(
    client: AsyncClient, factory: Factory, cases: int, owner: Principal
) -> str:
    dataset_id = await make_dataset(factory, cases, owner)
    response = await client.post(URL, json=payload(dataset_id))
    assert response.status_code == 201
    return str(response.json()["id"])


async def _run_all(factory: Factory, llm: FakeLLM, enqueued: Enqueued) -> None:
    runner = ExperimentRunner(
        scope_of(factory), llm.as_service(), 4, jitter=lambda: 0.0
    )
    for result_id in enqueued.flat:
        await runner.run(result_id)
    enqueued.batches.clear()


async def test_create_returns_201_and_persists_pending(
    client: AsyncClient,
    session_factory: Factory,
    enqueued: Enqueued,
    owner: Principal,
) -> None:
    dataset_id = await make_dataset(session_factory, 2, owner)
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
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    ids = [await _create(client, session_factory, 1, owner) for _ in range(3)]
    body = (await client.get(f"{URL}?limit=2&offset=2")).json()
    assert body["total"] == 3
    assert (body["limit"], body["offset"]) == (2, 2)
    assert [e["id"] for e in body["items"]] == ids[2:]


async def test_start_returns_202_and_enqueues(
    client: AsyncClient,
    session_factory: Factory,
    enqueued: Enqueued,
    owner: Principal,
) -> None:
    experiment_id = await _create(client, session_factory, 3, owner)
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
    owner: Principal,
) -> None:
    experiment_id = await _create(client, session_factory, 3, owner)
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
    assert ok["items"][0]["response_truncated"] is False


async def test_retry_failed_returns_202_only_failed(
    client: AsyncClient,
    session_factory: Factory,
    llm: FakeLLM,
    enqueued: Enqueued,
    owner: Principal,
) -> None:
    experiment_id = await _create(client, session_factory, 3, owner)
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
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    experiment_id = await _create(client, session_factory, 3, owner)
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
    owner: Principal,
) -> None:
    # Retry a pending experiment.
    experiment_id = await _create(client, session_factory, 3, owner)
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
    cancelled = await _create(client, session_factory, 3, owner)
    await client.post(f"{URL}/{cancelled}/cancel")
    for action in ("start", "cancel", "retry-failed"):
        response = await client.post(f"{URL}/{cancelled}/{action}")
        assert response.status_code == 409, action


async def test_start_twice_while_running_is_allowed(
    client: AsyncClient,
    session_factory: Factory,
    enqueued: Enqueued,
    owner: Principal,
) -> None:
    experiment_id = await _create(client, session_factory, 2, owner)
    await client.post(f"{URL}/{experiment_id}/start")
    again = await client.post(f"{URL}/{experiment_id}/start")
    assert again.status_code == 202
    assert again.json()["enqueued"] == 2


async def _loc(client: AsyncClient, body: dict[str, Any]) -> list[Any]:
    response = await client.post(URL, json=body)
    assert response.status_code == 422
    return list(response.json()["detail"][0]["loc"])


async def test_create_422_cases(
    client: AsyncClient,
    session_factory: Factory,
    llm: FakeLLM,
    owner: Principal,
) -> None:
    dataset_id = await make_dataset(session_factory, 2, owner)
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
    empty = await make_dataset(session_factory, 0, owner)
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
    client: AsyncClient, session_factory: Factory, path: str, owner: Principal
) -> None:
    experiment_id = await _create(client, session_factory, 1, owner)
    assert (await client.get(f"{URL}{path}")).status_code == 422
    response = await client.get(f"{URL}/{experiment_id}/results{path}")
    assert response.status_code == 422


async def test_invalid_status_filter_and_id_422(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    experiment_id = await _create(client, session_factory, 1, owner)
    response = await client.get(f"{URL}/{experiment_id}/results?status=bogus")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "status"]
    assert (await client.get(f"{URL}/not-a-uuid")).status_code == 422


async def test_valid_evaluator_config_is_stored_and_returned(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    dataset_id = await make_dataset(session_factory, 1, owner)
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


async def test_llm_judge_evaluator_config_is_stored_and_returned(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    dataset_id = await make_dataset(session_factory, 1, owner)
    config = {
        "evaluators": [
            {
                "id": "judge",
                "type": "llm_judge",
                "criteria": "response must be polite and on-topic",
                "provider": "openai",
                "model": "gpt-4o-mini",
            }
        ]
    }
    response = await client.post(
        URL, json=payload(dataset_id, evaluator_config=config)
    )
    assert response.status_code == 201
    stored = response.json()["evaluator_config"]["evaluators"]
    assert stored == [
        {
            "id": "judge",
            "type": "llm_judge",
            "criteria": "response must be polite and on-topic",
            "provider": "openai",
            "model": "gpt-4o-mini",
        }
    ]


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
        {
            "evaluators": [
                {"id": "j", "type": "llm_judge", "provider": "openai"}
            ]
        },
        {
            "evaluators": [
                {
                    "id": "j",
                    "type": "llm_judge",
                    "criteria": "ok",
                    "provider": "mistral",
                    "model": "m",
                }
            ]
        },
    ],
)
async def test_invalid_evaluator_config_is_422(
    client: AsyncClient,
    session_factory: Factory,
    evaluator_config: Any,
    owner: Principal,
) -> None:
    dataset_id = await make_dataset(session_factory, 1, owner)
    loc = await _loc(
        client, payload(dataset_id, evaluator_config=evaluator_config)
    )
    assert loc[:2] == ["body", "evaluator_config"]
    assert (await client.get(URL)).json()["total"] == 0


async def _as(actors: Actors, name: str) -> AsyncClient:
    return await actors.client(name)


async def _create_as(
    actors: Actors, factory: Factory, name: str, cases: int = 2
) -> str:
    return await _create(
        await _as(actors, name), factory, cases, actors.principal(name)
    )


async def test_create_records_owner_and_organization(
    actors: Actors, session_factory: Factory
) -> None:
    owner = actors.principal("staff_a")
    dataset_id = await make_dataset(session_factory, 1, owner)
    client = await _as(actors, "staff_a")
    response = await client.post(URL, json=payload(dataset_id))
    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == str(owner.user_id)
    assert body["organization_id"] == str(owner.organization_id)
    got = (await client.get(f"{URL}/{body['id']}")).json()
    assert got["owner_id"] == str(owner.user_id)


async def test_owner_in_request_body_is_ignored(
    actors: Actors, session_factory: Factory
) -> None:
    owner = actors.principal("staff_a")
    dataset_id = await make_dataset(session_factory, 1, owner)
    client = await _as(actors, "staff_a")
    forged = str(actors.principal("staff_b").user_id)
    response = await client.post(URL, json=payload(dataset_id, owner_id=forged))
    assert response.status_code == 201
    assert response.json()["owner_id"] == str(owner.user_id)


async def test_superadmin_cannot_create_experiments(
    actors: Actors, session_factory: Factory, client: AsyncClient
) -> None:
    dataset_id = await make_dataset(
        session_factory, 1, actors.principal("staff_a")
    )
    root = await _as(actors, "superadmin")
    response = await root.post(URL, json=payload(dataset_id))
    assert response.status_code == 403
    assert (await root.get(URL)).json()["total"] == 0


async def test_another_users_dataset_is_404_and_nothing_is_stored(
    actors: Actors, session_factory: Factory, enqueued: Enqueued
) -> None:
    dataset_id = await make_dataset(
        session_factory, 2, actors.principal("staff_a")
    )
    for name in ("staff_b", "admin_other"):
        client = await _as(actors, name)
        response = await client.post(URL, json=payload(dataset_id))
        assert response.status_code == 404
        unknown = await client.post(URL, json=payload(uuid.uuid4()))
        assert (
            response.json() == unknown.json() == {"detail": "Dataset not found"}
        )
    root = await _as(actors, "superadmin")
    assert (await root.get(URL)).json()["total"] == 0
    async with session_factory() as session:
        count = await session.scalar(select(func.count(ExperimentResult.id)))
    assert count == 0
    assert enqueued.flat == []


async def test_admin_of_the_organization_may_use_a_staff_dataset(
    actors: Actors, session_factory: Factory
) -> None:
    dataset_id = await make_dataset(
        session_factory, 1, actors.principal("staff_a")
    )
    admin = await _as(actors, "admin")
    response = await admin.post(URL, json=payload(dataset_id))
    assert response.status_code == 201
    body = response.json()
    # Owner is the caller, organization is the dataset's.
    assert body["owner_id"] == str(actors["admin"].user_id)
    assert body["organization_id"] == str(actors.org_id)


@pytest.mark.parametrize(
    ("name", "allowed"),
    [
        ("staff_a", True),
        ("staff_b", False),
        ("admin", True),
        ("admin_other", False),
        ("superadmin", True),
    ],
)
async def test_experiment_actions_are_scoped(
    actors: Actors,
    session_factory: Factory,
    name: str,
    allowed: bool,
) -> None:
    experiment_id = await _create_as(actors, session_factory, "staff_a")
    client = await _as(actors, name)
    missing = uuid.uuid4()
    for method, suffix in (
        ("get", ""),
        ("get", "/results"),
        ("post", "/start"),
        ("post", "/cancel"),
        ("post", "/retry-failed"),
    ):
        response = await getattr(client, method)(
            f"{URL}/{experiment_id}{suffix}"
        )
        if allowed:
            # 409 for retry-failed on a not yet failed experiment is fine:
            # the experiment is visible.
            assert response.status_code != 404, (method, suffix)
        else:
            unknown = await getattr(client, method)(f"{URL}/{missing}{suffix}")
            assert response.status_code == 404, (method, suffix)
            assert response.json() == unknown.json()


async def test_out_of_scope_actions_change_nothing(
    actors: Actors, session_factory: Factory, enqueued: Enqueued
) -> None:
    experiment_id = await _create_as(actors, session_factory, "staff_a")
    other = await _as(actors, "staff_b")
    for action in ("start", "cancel", "retry-failed"):
        assert (
            await other.post(f"{URL}/{experiment_id}/{action}")
        ).status_code == 404
    assert enqueued.flat == []
    owner = await _as(actors, "staff_a")
    got = (await owner.get(f"{URL}/{experiment_id}")).json()
    assert got["status"] == "pending"
    assert got["status_counts"] == {"pending": 2}


async def test_list_is_scoped_and_totals_reflect_scope(
    actors: Actors, session_factory: Factory
) -> None:
    a1 = await _create_as(actors, session_factory, "staff_a", 1)
    a2 = await _create_as(actors, session_factory, "staff_a", 1)
    b1 = await _create_as(actors, session_factory, "staff_b", 1)

    async def ids(name: str, query: str = "") -> tuple[list[str], int]:
        body = (await (await _as(actors, name)).get(f"{URL}{query}")).json()
        return [e["id"] for e in body["items"]], body["total"]

    assert await ids("staff_a") == ([a1, a2], 2)
    assert await ids("staff_b") == ([b1], 1)
    assert await ids("admin") == ([a1, a2, b1], 3)
    assert await ids("admin_other") == ([], 0)
    assert await ids("superadmin") == ([a1, a2, b1], 3)
    # Pagination totals are the visible total, not the table size.
    paged = (await (await _as(actors, "staff_a")).get(f"{URL}?limit=1")).json()
    assert paged["total"] == 2 and len(paged["items"]) == 1


async def test_owner_filter_narrows_but_never_widens(
    actors: Actors, session_factory: Factory
) -> None:
    a1 = await _create_as(actors, session_factory, "staff_a", 1)
    b1 = await _create_as(actors, session_factory, "staff_b", 1)
    staff_a = str(actors["staff_a"].user_id)
    staff_b = str(actors["staff_b"].user_id)

    async def listed(name: str, owner_id: str) -> list[str]:
        client = await _as(actors, name)
        body = (await client.get(f"{URL}?owner_id={owner_id}")).json()
        return [e["id"] for e in body["items"]]

    assert await listed("admin", staff_a) == [a1]
    assert await listed("admin", staff_b) == [b1]
    assert await listed("superadmin", staff_b) == [b1]
    assert await listed("staff_a", staff_a) == [a1]
    assert await listed("staff_a", staff_b) == []
    assert await listed("admin_other", staff_a) == []


async def _set_created(
    factory: Factory, experiment_id: str, when: datetime
) -> None:
    async with factory() as session:
        await session.execute(
            update(Experiment)
            .where(Experiment.id == uuid.UUID(experiment_id))
            .values(created_at=when)
        )
        await session.commit()


async def _set_status(factory: Factory, experiment_id: str, value: str) -> None:
    async with factory() as session:
        await session.execute(
            update(Experiment)
            .where(Experiment.id == uuid.UUID(experiment_id))
            .values(status=value)
        )
        await session.commit()


async def test_list_status_and_date_filters(
    client: AsyncClient,
    session_factory: Factory,
    owner: Principal,
) -> None:
    first = await _create(client, session_factory, 1, owner)
    second = await _create(client, session_factory, 1, owner)
    third = await _create(client, session_factory, 1, owner)
    await _set_status(session_factory, second, "completed")
    await _set_status(session_factory, third, "completed")
    day = datetime(2026, 3, 10, tzinfo=UTC)
    await _set_created(session_factory, first, day)
    await _set_created(session_factory, second, day + timedelta(days=1))
    await _set_created(session_factory, third, day + timedelta(days=2))

    async def listed(query: str) -> tuple[list[str], int]:
        body = (await client.get(f"{URL}?{query}")).json()
        return [e["id"] for e in body["items"]], body["total"]

    assert await listed("status=completed") == ([second, third], 2)
    assert await listed("status=pending") == ([first], 1)
    assert await listed("status=cancelled") == ([], 0)
    # Bounds are inclusive.
    assert await listed(
        "created_from=2026-03-10T00:00:00%2B00:00"
        "&created_to=2026-03-11T00:00:00%2B00:00"
    ) == ([first, second], 2)
    # Combined filters.
    assert await listed(
        "status=completed&created_from=2026-03-11T00:00:00%2B00:00"
        "&created_to=2026-03-11T00:00:00%2B00:00"
    ) == ([second], 1)
    assert await listed(
        "status=pending&created_from=2026-03-11T00:00:00%2B00:00"
    ) == ([], 0)


@pytest.mark.parametrize(
    ("query", "loc"),
    [
        ("status=bogus", "status"),
        ("created_from=2026-03-10T00:00:00", "created_from"),
        ("created_to=2026-03-10T00:00:00", "created_to"),
        (
            "created_from=2026-03-11T00:00:00%2B00:00"
            "&created_to=2026-03-10T00:00:00%2B00:00",
            "created_from",
        ),
        ("owner_id=not-a-uuid", "owner_id"),
    ],
)
async def test_list_filter_validation_is_422(
    client: AsyncClient, query: str, loc: str
) -> None:
    response = await client.get(f"{URL}?{query}")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", loc]


async def test_unauthenticated_requests_are_401(
    app: FastAPI, actors: Actors
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as anonymous:
        for method, path in (
            ("get", URL),
            ("post", URL),
            ("get", f"{URL}/{uuid.uuid4()}"),
            ("post", f"{URL}/{uuid.uuid4()}/start"),
        ):
            response = await getattr(anonymous, method)(path)
            assert response.status_code == 401, (method, path)
