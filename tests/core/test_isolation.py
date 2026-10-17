"""Role by resource by endpoint isolation matrix and log-leak checks over
every protected dataset, experiment, evaluation, metrics and export route.
Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from api.v1.endpoints import evaluations as evaluations_api
from api.v1.endpoints import experiments as experiments_api
from api.v1.endpoints import metrics as metrics_api
from components.datasets.models import TestCase
from components.experiments.models import Experiment, ExperimentResult
from components.users.models import User
from tests.access import PASSWORD, Actors, Factory
from tests.experiments.helpers import Enqueued, FakeLLM
from tests.metrics.helpers import Row, seed

pytestmark = pytest.mark.integration

ALLOWED = ["staff_a", "admin", "superadmin"]
DENIED = ["staff_b", "admin_other"]
PREFIX = "/api/v1"


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str  # may contain {ds} and {ex}
    ok: int
    body: dict[str, Any] | None = None
    # Experiment status that makes the action allowed for its owner.
    needs_status: str | None = None

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"


ITEM_ENDPOINTS = [
    Endpoint("GET", "/datasets/{ds}", 200),
    Endpoint("GET", "/datasets/{ds}/cases", 200),
    Endpoint("POST", "/datasets/{ds}/cases", 201, {"input": "new case"}),
    Endpoint("GET", "/experiments/{ex}", 200),
    Endpoint("GET", "/experiments/{ex}/results", 200),
    Endpoint("POST", "/experiments/{ex}/start", 202, None, "pending"),
    Endpoint("POST", "/experiments/{ex}/cancel", 200, None, "running"),
    Endpoint(
        "POST", "/experiments/{ex}/retry-failed", 202, None, "partially_failed"
    ),
    Endpoint("POST", "/experiments/{ex}/evaluate", 202),
    Endpoint("GET", "/experiments/{ex}/evaluations", 200),
    Endpoint("GET", "/experiments/{ex}/evaluations/summary", 200),
    Endpoint("GET", "/experiments/{ex}/summary", 200),
    Endpoint("GET", "/experiments/{ex}/comparison", 200),
    Endpoint("GET", "/experiments/{ex}/cases", 200),
    Endpoint("GET", "/experiments/{ex}/export?format=json", 200),
    Endpoint("GET", "/experiments/{ex}/export?format=csv", 200),
]
LIST_PATHS = ["/datasets", "/experiments"]


@dataclass
class World:
    experiment_id: uuid.UUID
    dataset_id: uuid.UUID
    enqueued: Enqueued

    def url(self, endpoint: Endpoint, unknown: bool = False) -> str:
        ds = uuid.uuid4() if unknown else self.dataset_id
        ex = uuid.uuid4() if unknown else self.experiment_id
        return PREFIX + endpoint.path.format(ds=ds, ex=ex)


@pytest.fixture
def enqueued() -> Enqueued:
    return Enqueued()


@pytest.fixture
async def world(
    app: FastAPI,
    actors: Actors,
    session_factory: Factory,
    enqueued: Enqueued,
) -> World:
    """A dataset and experiment owned by staff A (the ``actors`` fixture
    also wires the session and settings overrides)."""
    app.dependency_overrides[experiments_api.get_enqueue] = lambda: enqueued
    app.dependency_overrides[evaluations_api.get_enqueue] = lambda: enqueued
    app.dependency_overrides[experiments_api.get_llm_service] = (
        FakeLLM().as_service
    )
    for module in (evaluations_api, metrics_api):
        app.dependency_overrides[module.get_factory] = lambda: session_factory
    app.dependency_overrides[metrics_api.get_pricing_file] = lambda: None
    experiment_id = await seed(
        session_factory,
        [
            Row(1, text="one", evals={"ex": "passed"}),
            Row(2, status="failed", error_kind="timeout", attempts=3),
            Row(3, status="pending"),
        ],
        evaluators=("ex",),
        owner=actors.principal("staff_a"),
    )
    async with session_factory() as session:
        dataset_id = await session.scalar(
            select(Experiment.dataset_id).where(Experiment.id == experiment_id)
        )
    assert dataset_id is not None
    return World(experiment_id, dataset_id, enqueued)


async def _prepare(factory: Factory, world: World, endpoint: Endpoint) -> None:
    if endpoint.needs_status is None:
        return
    async with factory() as session:
        await session.execute(
            update(Experiment)
            .where(Experiment.id == world.experiment_id)
            .values(status=endpoint.needs_status)
        )
        await session.commit()


async def _send(client: AsyncClient, endpoint: Endpoint, url: str) -> Any:
    return await client.request(endpoint.method, url, json=endpoint.body)


@pytest.mark.parametrize("endpoint", ITEM_ENDPOINTS, ids=lambda e: e.id)
@pytest.mark.parametrize("actor", ALLOWED)
async def test_owner_admin_and_superadmin_are_allowed(
    actors: Actors,
    world: World,
    session_factory: Factory,
    endpoint: Endpoint,
    actor: str,
) -> None:
    await _prepare(session_factory, world, endpoint)
    client = await actors.client(actor)
    response = await _send(client, endpoint, world.url(endpoint))
    assert response.status_code == endpoint.ok, response.text


@pytest.mark.parametrize("endpoint", ITEM_ENDPOINTS, ids=lambda e: e.id)
@pytest.mark.parametrize("actor", DENIED)
async def test_other_staff_and_other_organization_see_404_like_unknown(
    actors: Actors,
    world: World,
    session_factory: Factory,
    endpoint: Endpoint,
    actor: str,
) -> None:
    # The resource is in the state that would allow the action, so only
    # access control can explain the 404.
    await _prepare(session_factory, world, endpoint)
    async with session_factory() as session:
        status_before = await session.scalar(
            select(Experiment.status).where(
                Experiment.id == world.experiment_id
            )
        )
    client = await actors.client(actor)
    unknown = await _send(client, endpoint, world.url(endpoint, unknown=True))
    denied = await _send(client, endpoint, world.url(endpoint))
    assert unknown.status_code == 404
    assert denied.status_code == 404
    assert denied.json() == unknown.json()
    assert world.enqueued.flat == []
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(Experiment.status).where(
                    Experiment.id == world.experiment_id
                )
            )
            == status_before
        )
        stored = (
            await session.execute(
                select(ExperimentResult.status).where(
                    ExperimentResult.experiment_id == world.experiment_id
                )
            )
        ).scalars()
        assert sorted(stored) == ["failed", "pending", "succeeded"]
        cases = (
            await session.execute(
                select(TestCase.input).where(
                    TestCase.dataset_id == world.dataset_id
                )
            )
        ).scalars()
        assert "new case" not in list(cases)


@pytest.mark.parametrize("endpoint", ITEM_ENDPOINTS, ids=lambda e: e.id)
@pytest.mark.parametrize("token", [None, "not-a-token"])
async def test_no_or_bad_token_is_401(
    actors: Actors, world: World, endpoint: Endpoint, token: str | None
) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(
        transport=ASGITransport(app=actors.app),
        base_url="http://test",
        headers=headers,
    ) as anonymous:
        response = await _send(anonymous, endpoint, world.url(endpoint))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path", LIST_PATHS)
@pytest.mark.parametrize(
    ("actor", "visible"),
    [
        ("staff_a", True),
        ("admin", True),
        ("superadmin", True),
        ("staff_b", False),
        ("admin_other", False),
    ],
)
async def test_listings_contain_the_resource_only_for_allowed_roles(
    actors: Actors, world: World, path: str, actor: str, visible: bool
) -> None:
    client = await actors.client(actor)
    response = await client.get(f"{PREFIX}{path}")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    expected = world.dataset_id if path == "/datasets" else world.experiment_id
    assert (str(expected) in ids) is visible
    if not visible:
        assert response.json()["total"] == 0


@pytest.mark.parametrize("path", LIST_PATHS)
async def test_listing_without_token_is_401(
    actors: Actors, world: World, path: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=actors.app), base_url="http://test"
    ) as anonymous:
        assert (await anonymous.get(f"{PREFIX}{path}")).status_code == 401


def _experiment_body(dataset_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "name": "exp",
        "dataset_id": str(dataset_id),
        "prompt_template": "Q: {input}",
        "config": {"targets": [{"provider": "openai", "model": "m1"}]},
        **extra,
    }


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("staff_a", 201),
        ("staff_b", 201),
        ("admin", 201),
        ("admin_other", 201),
        ("superadmin", 403),
    ],
)
async def test_dataset_creation_ignores_forged_ownership_fields(
    actors: Actors, actor: str, expected: int
) -> None:
    other_org, other_user = actors.other_org_id, actors["staff_b"].user_id
    forged = {
        "name": "mine",
        "owner_id": str(other_user),
        "organization_id": str(other_org),
    }
    client = await actors.client(actor)
    response = await client.post(f"{PREFIX}/datasets", json=forged)
    assert response.status_code == expected
    if expected == 201:
        assert response.json()["owner_id"] == str(actors[actor].user_id)
        assert response.json()["organization_id"] == str(
            actors[actor].organization_id
        )


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("staff_a", 201),
        ("admin", 201),
        ("superadmin", 403),
        ("staff_b", 404),
        ("admin_other", 404),
    ],
)
async def test_experiment_creation_matrix_and_forged_fields(
    actors: Actors, world: World, actor: str, expected: int
) -> None:
    client = await actors.client(actor)
    forged = {
        "owner_id": str(actors["staff_b"].user_id),
        "organization_id": str(actors.other_org_id),
    }
    response = await client.post(
        f"{PREFIX}/experiments",
        json=_experiment_body(world.dataset_id, **forged),
    )
    assert response.status_code == expected, response.text
    if expected == 201:
        assert response.json()["owner_id"] == str(actors[actor].user_id)
        assert response.json()["organization_id"] == str(
            actors[actor].organization_id
        )
    if expected == 404:
        unknown = await client.post(
            f"{PREFIX}/experiments", json=_experiment_body(uuid.uuid4())
        )
        assert unknown.status_code == 404
        assert response.json() == unknown.json()


@pytest.mark.parametrize("path", ["/datasets", "/experiments"])
async def test_creation_without_token_is_401(
    actors: Actors, world: World, path: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=actors.app), base_url="http://test"
    ) as anonymous:
        response = await anonymous.post(
            f"{PREFIX}{path}", json=_experiment_body(world.dataset_id)
        )
    assert response.status_code == 401


async def test_nothing_sensitive_reaches_debug_logs(
    actors: Actors,
    world: World,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tokens, passwords, password hashes and resource content stay out of
    logs for allowed and denied requests alike."""
    sentinels = {
        "input": "SENTINEL-CASE-INPUT-7f3a",
        "expected": "SENTINEL-EXPECTED-91bc",
        "response": "SENTINEL-RESPONSE-2d5e",
        "prompt": "SENTINEL-PROMPT-c04f",
        "api_input": "SENTINEL-API-INPUT-5a60",
    }
    wrong_password = "SENTINEL-WRONG-PASSWORD-b8d2"
    async with session_factory() as session:
        await session.execute(
            update(TestCase)
            .where(TestCase.dataset_id == world.dataset_id)
            .values(
                input=sentinels["input"], expected_output=sentinels["expected"]
            )
        )
        await session.execute(
            update(ExperimentResult)
            .where(ExperimentResult.experiment_id == world.experiment_id)
            .values(
                response_text=sentinels["response"],
                rendered_prompt=sentinels["prompt"],
            )
        )
        await session.commit()
        hashes = list(
            (await session.execute(select(User.password_hash))).scalars()
        )
    assert hashes

    caplog.set_level(logging.DEBUG)
    logging.getLogger("tests.canary").debug("canary")
    secrets = [PASSWORD, wrong_password, *hashes]

    async with AsyncClient(
        transport=ASGITransport(app=actors.app), base_url="http://test"
    ) as anonymous:
        bad = await anonymous.post(
            f"{PREFIX}/auth/login",
            json={"email": actors["staff_a"].email, "password": wrong_password},
        )
        assert bad.status_code == 401
        login = await anonymous.post(
            f"{PREFIX}/auth/login",
            json={"email": actors["staff_a"].email, "password": PASSWORD},
        )
        refresh = login.json()["refresh_token"]
        secrets += [refresh, login.json()["access_token"]]
        rotated = await anonymous.post(
            f"{PREFIX}/auth/refresh", json={"refresh_token": refresh}
        )
        secrets += [
            rotated.json()["refresh_token"],
            rotated.json()["access_token"],
        ]
        # Reuse of a retired token, then a request with a garbage token.
        await anonymous.post(
            f"{PREFIX}/auth/refresh", json={"refresh_token": refresh}
        )
        await anonymous.get(
            f"{PREFIX}/datasets",
            headers={"Authorization": "Bearer SENTINEL-GARBAGE-TOKEN-1e77"},
        )
        secrets.append("SENTINEL-GARBAGE-TOKEN-1e77")

    for actor in ("staff_a", "staff_b", "admin", "admin_other", "superadmin"):
        client = await actors.client(actor)
        secrets.append((await actors.headers(actor))["Authorization"][7:])
        for endpoint in ITEM_ENDPOINTS:
            body = endpoint.body and {"input": sentinels["api_input"]}
            await client.request(
                endpoint.method, world.url(endpoint), json=body
            )
            await client.request(
                endpoint.method, world.url(endpoint, unknown=True), json=body
            )
        for path in LIST_PATHS:
            await client.get(f"{PREFIX}{path}")
        await client.post(
            f"{PREFIX}/datasets",
            json={"name": "n", "description": sentinels["api_input"]},
        )

    captured = caplog.text + "".join(r.getMessage() for r in caplog.records)
    assert "canary" in captured  # capture works
    for secret in [*secrets, *sentinels.values()]:
        assert secret not in captured
