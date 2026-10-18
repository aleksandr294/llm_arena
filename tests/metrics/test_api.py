"""API tests over seeded data: need PostgreSQL (``DATABASE_URL``)."""

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.endpoints.metrics import get_pricing_file, get_session_scope
from components.core.database import get_session, session_scope
from components.core.schemas import Principal
from tests.access import Actors
from tests.metrics.helpers import Row, seed

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def _overrides(app: FastAPI, session_factory: Factory) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_scope(session_factory) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_session_scope] = lambda: partial(
        session_scope, session_factory
    )
    app.dependency_overrides[get_pricing_file] = lambda: None


@pytest.fixture
async def client(actors: Actors) -> AsyncClient:
    """Authenticated as staff user A; shadows the anonymous root client."""
    return await actors.client("staff_a")


@pytest.fixture
def owner(actors: Actors) -> Principal:
    return actors.principal("staff_a")


def url(experiment_id: uuid.UUID, suffix: str) -> str:
    return f"/api/v1/experiments/{experiment_id}/{suffix}"


def _rows() -> list[Row]:
    return [
        Row(
            1,
            latency=100.0,
            tokens=(10, 20, 30),
            text="one",
            evals={"ex": "passed"},
        ),
        Row(
            2,
            latency=300.0,
            tokens=(10, 20, 30),
            text="two",
            evals={"ex": "failed"},
        ),
        Row(3, status="failed", error_kind="timeout", attempts=3),
        Row(4, status="pending"),
        Row(1, model="m2", latency=50.0, tokens=(5, 5, 10), text="x"),
    ]


async def _seed(factory: Factory, owner: Principal) -> uuid.UUID:
    return await seed(
        factory,
        _rows(),
        models=("m1", "m2"),
        evaluators=("ex",),
        owner=owner,
    )


async def test_summary(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    experiment_id = await _seed(session_factory, owner)
    response = await client.get(url(experiment_id, "summary"))
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {
        "pending": 1,
        "running": 0,
        "succeeded": 3,
        "failed": 1,
        "cancelled": 0,
        "total": 5,
    }
    assert body["finished"] is False


async def test_comparison_matches_stored_rows(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    experiment_id = await _seed(session_factory, owner)
    response = await client.get(url(experiment_id, "comparison"))
    assert response.status_code == 200
    targets = {t["model"]: t for t in response.json()["targets"]}
    m1 = targets["m1"]
    assert m1["error_rate"] == pytest.approx(1 / 3)
    assert m1["error_kinds"] == {"timeout": 1}
    assert m1["latency"]["count"] == 2
    assert m1["latency"]["mean_ms"] == 200.0
    assert m1["tokens"]["total_tokens"]["total"] == 60
    assert m1["evaluators"][0]["pass_rate"] == 0.5
    assert m1["cost"]["priced"] is False
    assert targets["m2"]["error_rate"] == 0.0

    # The same numbers derived from the cases view and the JSON export.
    cases = (
        await client.get(url(experiment_id, "cases"), params={"limit": 100})
    ).json()
    flat = [r for c in cases["items"] for r in c["results"]]
    for model, target in targets.items():
        mine = [r for r in flat if r["model"] == model]
        for status in ("succeeded", "failed", "pending"):
            assert target["counts"][status] == sum(
                r["status"] == status for r in mine
            )
        assert (
            target["tokens"]["total_tokens"]["total"]
            == sum(r["total_tokens"] or 0 for r in mine)
            or target["tokens"]["total_tokens"]["total"] is None
        )
    exported = json.loads((await client.get(url(experiment_id, "export"))).text)
    assert exported["comparison"] == response.json()
    assert len(exported["results"]) == len(flat) == 5


async def test_cases_pagination_and_response_rules(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    experiment_id = await _seed(session_factory, owner)
    page = (
        await client.get(url(experiment_id, "cases"), params={"limit": 2})
    ).json()
    assert page["total"] == 4
    assert [c["case_position"] for c in page["items"]] == [1, 2]
    assert all(
        r["response_text"] is None for c in page["items"] for r in c["results"]
    )
    page2 = (
        await client.get(
            url(experiment_id, "cases"),
            params={"limit": 2, "offset": 2, "include_responses": "true"},
        )
    ).json()
    assert [c["case_position"] for c in page2["items"]] == [3, 4]
    with_text = (
        await client.get(
            url(experiment_id, "cases"), params={"include_responses": "true"}
        )
    ).json()
    texts = {
        r["response_text"]
        for c in with_text["items"]
        for r in c["results"]
        if r["response_text"]
    }
    assert texts == {"one", "two", "x"}


@pytest.mark.parametrize("suffix", ["summary", "comparison", "cases", "export"])
async def test_unknown_experiment_is_404(
    client: AsyncClient, suffix: str
) -> None:
    response = await client.get(url(uuid.uuid4(), suffix))
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("suffix", "params"),
    [
        ("cases", {"limit": 0}),
        ("cases", {"limit": 101}),
        ("cases", {"offset": -1}),
        ("cases", {"limit": "x"}),
        ("cases", {"include_responses": "maybe"}),
        ("export", {"format": "xml"}),
        ("export", {"include_responses": "maybe"}),
    ],
)
async def test_bad_query_values_are_422(
    client: AsyncClient,
    session_factory: Factory,
    suffix: str,
    params: dict[str, object],
    owner: Principal,
) -> None:
    experiment_id = await _seed(session_factory, owner)
    response = await client.get(url(experiment_id, suffix), params=params)  # type: ignore[arg-type]
    assert response.status_code == 422


async def test_invalid_experiment_id_is_422(client: AsyncClient) -> None:
    response = await client.get("/api/v1/experiments/not-a-uuid/summary")
    assert response.status_code == 422


async def test_json_export_defaults_and_headers(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    experiment_id = await _seed(session_factory, owner)
    response = await client.get(url(experiment_id, "export"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="experiment-{experiment_id}.json"'
    )
    doc = response.json()
    assert doc["results"][0]["response_text"] == "one"
    assert doc["results"][0]["rendered_prompt"] == "SECRET-PROMPT-1"
    hidden = (
        await client.get(
            url(experiment_id, "export"),
            params={"format": "json", "include_responses": "false"},
        )
    ).json()
    assert "response_text" not in hidden["results"][0]
    assert "rendered_prompt" not in hidden["results"][0]


async def test_csv_export_defaults_and_headers(
    client: AsyncClient, session_factory: Factory, owner: Principal
) -> None:
    experiment_id = await _seed(session_factory, owner)
    response = await client.get(
        url(experiment_id, "export"), params={"format": "csv"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="experiment-{experiment_id}.csv"'
    )
    table = list(csv.reader(io.StringIO(response.text, newline="")))
    assert "response_text" not in table[0]
    assert "eval:ex" in table[0]
    assert len(table) == 6
    with_text = await client.get(
        url(experiment_id, "export"),
        params={"format": "csv", "include_responses": "true"},
    )
    assert "response_text" in with_text.text.splitlines()[0]


async def test_pricing_file_dependency_feeds_cost(
    app: FastAPI,
    client: AsyncClient,
    session_factory: Factory,
    tmp_path: Path,
    owner: Principal,
) -> None:
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps(
            {
                "version": "v9",
                "currency": "USD",
                "rates": {
                    "openai": {
                        "m1": {
                            "input_per_million": 1,
                            "output_per_million": 2,
                        }
                    }
                },
            }
        )
    )
    app.dependency_overrides[get_pricing_file] = lambda: str(path)
    experiment_id = await _seed(session_factory, owner)
    body = (await client.get(url(experiment_id, "comparison"))).json()
    assert body["pricing"] == {"version": "v9", "currency": "USD"}
    m1 = next(t for t in body["targets"] if t["model"] == "m1")
    assert m1["cost"]["priced"] is True
    assert m1["cost"]["pricing_version"] == "v9"
    m2 = next(t for t in body["targets"] if t["model"] == "m2")
    assert m2["cost"]["priced"] is False


ACTORS = [
    ("staff_a", True),
    ("staff_b", False),
    ("admin", True),
    ("admin_other", False),
    ("superadmin", True),
]


@pytest.mark.parametrize(("name", "allowed"), ACTORS)
async def test_metrics_and_export_are_scoped_by_experiment(
    actors: Actors, session_factory: Factory, name: str, allowed: bool
) -> None:
    experiment_id = await _seed(session_factory, actors.principal("staff_a"))
    caller = await actors.client(name)
    for suffix in (
        "summary",
        "comparison",
        "cases",
        "cases?include_responses=true",
        "export",
        "export?format=json",
        "export?format=csv",
        "export?format=csv&include_responses=true",
    ):
        response = await caller.get(url(experiment_id, suffix))
        if allowed:
            assert response.status_code == 200, suffix
        else:
            unknown = await caller.get(url(uuid.uuid4(), suffix))
            assert response.status_code == 404, suffix
            assert response.json() == unknown.json()
            # Nothing was streamed: a plain error body, no attachment.
            assert "content-disposition" not in response.headers
            assert response.headers["content-type"] == "application/json"
            assert "SECRET" not in response.text
