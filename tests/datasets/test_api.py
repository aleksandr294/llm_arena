"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.database import get_session
from components.datasets.models import Dataset
from tests.access import Actors

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


@pytest.fixture
async def client(actors: Actors) -> AsyncClient:
    """Authenticated as staff user A; shadows the anonymous root client."""
    return await actors.client("staff_a")


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
    assert set(body) == {
        "id",
        "name",
        "description",
        "owner_id",
        "organization_id",
        "created_at",
    }

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


URL = "/api/v1/datasets"


async def _legacy(factory: async_sessionmaker, org: uuid.UUID) -> str:  # type: ignore[type-arg]
    """A dataset without an owner, as left by the 0006 migration."""
    async with factory() as session:
        dataset = Dataset(name="legacy", organization_id=org)
        session.add(dataset)
        await session.commit()
        return str(dataset.id)


async def _ids(client: AsyncClient, query: str = "") -> list[str]:
    response = await client.get(f"{URL}{query}")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(body["items"])
    return [d["id"] for d in body["items"]]


async def test_endpoints_require_authentication(app: FastAPI) -> None:
    anonymous = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )
    some = uuid.uuid4()
    for method, path in (
        ("get", URL),
        ("post", URL),
        ("get", f"{URL}/{some}"),
        ("get", f"{URL}/{some}/cases"),
        ("post", f"{URL}/{some}/cases"),
    ):
        response = await anonymous.request(method, path, json={})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    await anonymous.aclose()


async def test_owner_and_organization_recorded(
    client: AsyncClient, actors: Actors
) -> None:
    body = (await client.post(URL, json={"name": "x"})).json()
    assert body["owner_id"] == str(actors["staff_a"].user_id)
    assert body["organization_id"] == str(actors.org_id)
    admin = await actors.client("admin")
    made = (await admin.post(URL, json={"name": "y"})).json()
    assert made["owner_id"] == str(actors["admin"].user_id)


async def test_body_cannot_override_owner(
    client: AsyncClient, actors: Actors
) -> None:
    response = await client.post(
        URL,
        json={
            "name": "x",
            "owner_id": str(actors["staff_b"].user_id),
            "organization_id": str(actors.other_org_id),
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == str(actors["staff_a"].user_id)
    assert body["organization_id"] == str(actors.org_id)


async def test_superadmin_create_is_403(actors: Actors) -> None:
    superadmin = await actors.client("superadmin")
    response = await superadmin.post(URL, json={"name": "x"})
    assert response.status_code == 403
    assert "superadmin" in response.json()["detail"]
    assert (await superadmin.get(URL)).json()["total"] == 0


async def test_staff_isolation(client: AsyncClient, actors: Actors) -> None:
    mine = await _create(client, "mine")
    staff_b = await actors.client("staff_b")
    theirs = await _create(staff_b, "theirs")
    assert await _ids(client) == [mine]
    assert await _ids(staff_b) == [theirs]


async def test_admin_sees_own_organization_only(
    client: AsyncClient, actors: Actors
) -> None:
    a = await _create(client)
    b = await _create(await actors.client("staff_b"))
    admin_other = await actors.client("admin_other")
    other = await _create(admin_other)
    assert await _ids(await actors.client("admin")) == [a, b]
    assert await _ids(admin_other) == [other]


async def test_superadmin_sees_all(client: AsyncClient, actors: Actors) -> None:
    a = await _create(client)
    other = await _create(await actors.client("admin_other"))
    assert await _ids(await actors.client("superadmin")) == [a, other]


async def test_out_of_scope_is_404_identical_to_missing(
    client: AsyncClient, actors: Actors
) -> None:
    dataset_id = await _create(client)
    await client.post(f"{URL}/{dataset_id}/cases", json={"input": "a"})
    missing = str(uuid.uuid4())
    for name in ("staff_b", "admin_other"):
        other = await actors.client(name)
        for path, method, payload in (
            ("", "get", None),
            ("/cases", "get", None),
            ("/cases", "post", {"input": "b"}),
        ):
            real = await other.request(
                method, f"{URL}/{dataset_id}{path}", json=payload
            )
            gone = await other.request(
                method, f"{URL}/{missing}{path}", json=payload
            )
            assert real.status_code == gone.status_code == 404
            assert real.json() == gone.json()
    # Nothing was added by the denied write.
    cases = await client.get(f"{URL}/{dataset_id}/cases")
    assert cases.json()["total"] == 1
    for name in ("admin", "superadmin"):
        who = await actors.client(name)
        assert (await who.get(f"{URL}/{dataset_id}")).status_code == 200
        added = await who.post(f"{URL}/{dataset_id}/cases", json={"input": "c"})
        assert added.status_code == 201
        assert (await who.get(f"{URL}/{dataset_id}/cases")).status_code == 200


async def test_legacy_dataset_visibility(
    client: AsyncClient,
    actors: Actors,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    legacy = await _legacy(session_factory, actors.org_id)
    assert await _ids(client) == []
    assert (await client.get(f"{URL}/{legacy}")).status_code == 404
    admin = await actors.client("admin")
    assert await _ids(admin) == [legacy]
    detail = (await admin.get(f"{URL}/{legacy}")).json()
    assert detail["owner_id"] is None
    assert await _ids(await actors.client("superadmin")) == [legacy]
    assert await _ids(await actors.client("admin_other")) == []


async def test_date_filters(
    client: AsyncClient,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    ids = [await _create(client, f"d{i}") for i in range(3)]
    stamps = ["2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"]
    async with session_factory() as session:
        for dataset_id, stamp in zip(
            ids, [*stamps, "2026-03-01T00:00:00Z"], strict=True
        ):
            await session.execute(
                update(Dataset)
                .where(Dataset.id == uuid.UUID(dataset_id))
                .values(created_at=datetime.fromisoformat(stamp))
            )
        await session.commit()

    assert await _ids(client, "?created_from=2026-02-01T00:00:00Z") == ids[1:]
    assert await _ids(client, "?created_to=2026-02-01T00:00:00Z") == ids[:2]
    both = "?created_from=2026-02-01T00:00:00Z&created_to=2026-02-01T00:00:00Z"
    assert await _ids(client, both) == [ids[1]]
    offset = "?created_from=2026-02-01T02:00:00%2B02:00"  # == 00:00 UTC
    assert await _ids(client, offset) == ids[1:]
    page = await client.get(f"{URL}?created_from=2026-02-01T00:00:00Z&limit=1")
    assert page.json()["total"] == 2 and len(page.json()["items"]) == 1


@pytest.mark.parametrize(
    "query",
    [
        "created_from=2026-02-01T00:00:00",
        "created_to=2026-02-01",
        "created_from=2026-03-01T00:00:00Z&created_to=2026-02-01T00:00:00Z",
        "created_from=garbage",
    ],
)
async def test_invalid_date_filters_422(
    client: AsyncClient, query: str
) -> None:
    assert (await client.get(f"{URL}?{query}")).status_code == 422


async def test_owner_filter(client: AsyncClient, actors: Actors) -> None:
    mine = await _create(client)
    staff_b = await actors.client("staff_b")
    theirs = await _create(staff_b)
    a_id, b_id = actors["staff_a"].user_id, actors["staff_b"].user_id
    admin = await actors.client("admin")
    assert await _ids(admin, f"?owner_id={b_id}") == [theirs]
    assert await _ids(admin, f"?owner_id={a_id}") == [mine]
    superadmin = await actors.client("superadmin")
    assert await _ids(superadmin, f"?owner_id={a_id}") == [mine]
    # Staff cannot widen their scope through the filter.
    assert await _ids(client, f"?owner_id={b_id}") == []
    assert await _ids(client, f"?owner_id={a_id}") == [mine]
    # Another organization's admin gets nothing for this owner.
    other = await actors.client("admin_other")
    assert await _ids(other, f"?owner_id={a_id}") == []


async def test_totals_reflect_scope_and_filters(
    client: AsyncClient, actors: Actors
) -> None:
    for i in range(3):
        await _create(client, f"d{i}")
    await _create(await actors.client("staff_b"))
    body = (await client.get(f"{URL}?limit=1")).json()
    assert body["total"] == 3
    body = (await (await actors.client("admin")).get(f"{URL}?limit=1")).json()
    assert body["total"] == 4
