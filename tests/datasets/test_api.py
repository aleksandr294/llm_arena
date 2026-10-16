"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.database import get_session

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _use_scratch_db(
    app: FastAPI,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    async def override() -> AsyncIterator[AsyncSession]:
        # Same unit-of-work semantics as get_session.
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override


async def _create(client: AsyncClient, name: str = "d") -> str:
    response = await client.post("/api/v1/datasets", json={"name": name})
    assert response.status_code == 201
    return str(response.json()["id"])


async def test_create_and_get_dataset(client: AsyncClient) -> None:
    response = await client.post("/api/v1/datasets", json={"name": "  qa  "})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "qa"
    assert body["description"] is None
    assert set(body) == {"id", "name", "description", "created_at"}

    got = await client.get(f"/api/v1/datasets/{body['id']}")
    assert got.status_code == 200
    assert got.json()["case_count"] == 0


async def test_case_count_on_detail(client: AsyncClient) -> None:
    dataset_id = await _create(client)
    for text in ("a", "b"):
        await client.post(
            f"/api/v1/datasets/{dataset_id}/cases", json={"input": text}
        )
    got = await client.get(f"/api/v1/datasets/{dataset_id}")
    assert got.json()["case_count"] == 2


async def test_unknown_dataset_is_404(client: AsyncClient) -> None:
    missing = uuid.uuid4()
    for response in (
        await client.get(f"/api/v1/datasets/{missing}"),
        await client.get(f"/api/v1/datasets/{missing}/cases"),
        await client.post(
            f"/api/v1/datasets/{missing}/cases", json={"input": "a"}
        ),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Dataset not found"}


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/datasets", {"name": "   "}),
        ("post", "/api/v1/datasets", {}),
        ("post", "/api/v1/datasets", {"name": "x" * 256}),
        ("get", "/api/v1/datasets/not-a-uuid", None),
        ("get", "/api/v1/datasets?limit=0", None),
        ("get", "/api/v1/datasets?limit=101", None),
        ("get", "/api/v1/datasets?offset=-1", None),
    ],
)
async def test_dataset_422(
    client: AsyncClient, method: str, path: str, payload: dict | None  # type: ignore[type-arg]
) -> None:
    response = await client.request(method, path, json=payload)
    assert response.status_code == 422


async def test_blank_name_error_names_field(client: AsyncClient) -> None:
    response = await client.post("/api/v1/datasets", json={"name": " "})
    assert response.json()["detail"][0]["loc"] == ["body", "name"]


@pytest.mark.parametrize(
    "payload",
    [
        {"input": ""},
        {},
        {"input": "a", "metadata": [1]},
        {"input": "a", "metadata": {"k": "\u0000"}},
    ],
)
async def test_case_422(client: AsyncClient, payload: dict) -> None:  # type: ignore[type-arg]
    dataset_id = await _create(client)
    response = await client.post(
        f"/api/v1/datasets/{dataset_id}/cases", json=payload
    )
    assert response.status_code == 422
    listed = await client.get(f"/api/v1/datasets/{dataset_id}/cases")
    assert listed.json()["total"] == 0


async def test_empty_input_error_names_field(client: AsyncClient) -> None:
    dataset_id = await _create(client)
    response = await client.post(
        f"/api/v1/datasets/{dataset_id}/cases", json={"input": ""}
    )
    assert response.json()["detail"][0]["loc"] == ["body", "input"]


async def test_malformed_dataset_id_on_cases_422(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/datasets/x/cases")).status_code == 422


async def test_dataset_pagination_and_order(client: AsyncClient) -> None:
    ids = [await _create(client, f"d{i}") for i in range(3)]

    page = await client.get("/api/v1/datasets?limit=2&offset=2")
    body = page.json()
    assert body["total"] == 3
    assert (body["limit"], body["offset"]) == (2, 2)
    assert [d["id"] for d in body["items"]] == ids[2:]

    default = (await client.get("/api/v1/datasets")).json()
    assert default["limit"] == 50
    assert [d["id"] for d in default["items"]] == ids


async def test_cases_ordered_and_paginated(client: AsyncClient) -> None:
    dataset_id = await _create(client)
    for text in ("a", "b", "c"):
        response = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": text, "metadata": {"t": text}},
        )
        assert response.status_code == 201
    body = (await client.get(f"/api/v1/datasets/{dataset_id}/cases")).json()
    assert body["total"] == 3
    assert [c["position"] for c in body["items"]] == [1, 2, 3]
    assert [c["input"] for c in body["items"]] == ["a", "b", "c"]
    assert body["items"][0]["metadata"] == {"t": "a"}
    assert body["items"][0]["expected_output"] is None

    page = (
        await client.get(
            f"/api/v1/datasets/{dataset_id}/cases?limit=1&offset=1"
        )
    ).json()
    assert [c["input"] for c in page["items"]] == ["b"]
    assert page["total"] == 3


async def test_markup_stored_unchanged_and_not_logged(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "SECRET-MARKER-9f3a"
    text = f"<script>alert('{marker}')</script> {{{{ 7*7 }}}} ${{x}} {{% if %}}"
    caplog.set_level(logging.DEBUG)

    dataset_id = await _create(client, f"name-{marker}")
    response = await client.post(
        f"/api/v1/datasets/{dataset_id}/cases",
        json={"input": text, "expected_output": text, "metadata": {"m": text}},
    )
    assert response.status_code == 201
    listed = (await client.get(f"/api/v1/datasets/{dataset_id}/cases")).json()
    case = listed["items"][0]
    assert case["input"] == text
    assert case["expected_output"] == text
    assert case["metadata"] == {"m": text}
    await client.post("/api/v1/datasets", json={"name": ""})  # error path

    assert marker not in caplog.text


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
async def test_case_list_invalid_pagination_422(
    client: AsyncClient, query: str
) -> None:
    dataset_id = await _create(client)
    response = await client.get(f"/api/v1/datasets/{dataset_id}/cases?{query}")
    assert response.status_code == 422


async def test_add_case_response_positions_and_null_expected(
    client: AsyncClient,
) -> None:
    dataset_id = await _create(client)
    url = f"/api/v1/datasets/{dataset_id}/cases"
    first = await client.post(url, json={"input": "a"})
    second = await client.post(url, json={"input": "b"})
    assert (first.status_code, second.status_code) == (201, 201)
    assert (first.json()["position"], second.json()["position"]) == (1, 2)
    assert first.json()["expected_output"] is None


async def test_content_preserved_through_api(client: AsyncClient) -> None:
    dataset_id = await _create(client)
    text = '  "quoted"\nline two\tпривіт 日本\U0001f600  '
    await client.post(
        f"/api/v1/datasets/{dataset_id}/cases", json={"input": text}
    )
    listed = (await client.get(f"/api/v1/datasets/{dataset_id}/cases")).json()
    assert listed["items"][0]["input"] == text


async def test_description_limit_422_names_field(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/datasets", json={"name": "n", "description": "x" * 2001}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "description"]
