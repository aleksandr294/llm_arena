"""Organization management API tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from components.auth.models import RefreshToken
from components.core.access import Role
from tests.auth.conftest import Factory, login_headers, seed_user

pytestmark = pytest.mark.integration

URL = "/api/v1/organizations"


@pytest.fixture(autouse=True)
def _api(api: None) -> None:
    return None


async def _superadmin(client: AsyncClient, factory: Factory) -> dict[str, str]:
    user = await seed_user(factory, "root@example.com", Role.SUPERADMIN)
    return await login_headers(client, user)


async def test_create_and_list(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    response = await client.post(URL, json={"name": "Acme"}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme" and body["is_active"] is True
    listing = (await client.get(URL, headers=headers)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == body["id"]
    assert listing["limit"] == 50 and listing["offset"] == 0


async def test_nul_in_name_is_422(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    response = await client.post(
        URL, json={"name": "Ac\u0000me"}, headers=headers
    )
    assert response.status_code == 422
    created = await client.post(URL, json={"name": "Acme"}, headers=headers)
    response = await client.patch(
        f"{URL}/{created.json()['id']}",
        json={"name": "A\u0000"},
        headers=headers,
    )
    assert response.status_code == 422


async def test_duplicate_name_ignoring_case(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    await client.post(URL, json={"name": "Acme"}, headers=headers)
    response = await client.post(URL, json={"name": "ACME "}, headers=headers)
    assert response.status_code == 409


async def test_rename_to_existing_name_conflicts(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    await client.post(URL, json={"name": "Acme"}, headers=headers)
    other = (
        await client.post(URL, json={"name": "Beta"}, headers=headers)
    ).json()
    response = await client.patch(
        f"{URL}/{other['id']}", json={"name": "acme"}, headers=headers
    )
    assert response.status_code == 409
    # Changing only the case of its own name is allowed.
    same = await client.patch(
        f"{URL}/{other['id']}", json={"name": "BETA"}, headers=headers
    )
    assert same.status_code == 200 and same.json()["name"] == "BETA"


async def test_get_update_and_not_found(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    org = (
        await client.post(URL, json={"name": "Acme"}, headers=headers)
    ).json()
    got = await client.get(f"{URL}/{org['id']}", headers=headers)
    assert got.status_code == 200 and got.json()["name"] == "Acme"
    updated = await client.patch(
        f"{URL}/{org['id']}",
        json={"name": "Acme 2", "is_active": False},
        headers=headers,
    )
    assert updated.json()["name"] == "Acme 2"
    assert updated.json()["is_active"] is False
    missing = uuid.uuid4()
    assert (
        await client.get(f"{URL}/{missing}", headers=headers)
    ).status_code == 404
    patch = await client.patch(
        f"{URL}/{missing}", json={"name": "x"}, headers=headers
    )
    assert patch.status_code == 404


async def test_no_delete(client: AsyncClient, session_factory: Factory) -> None:
    headers = await _superadmin(client, session_factory)
    org = (
        await client.post(URL, json={"name": "Acme"}, headers=headers)
    ).json()
    response = await client.delete(f"{URL}/{org['id']}", headers=headers)
    assert response.status_code == 405


@pytest.mark.parametrize("role", [Role.ADMIN, Role.STAFF])
async def test_forbidden_for_non_superadmin(
    client: AsyncClient, session_factory: Factory, role: Role
) -> None:
    user = await seed_user(session_factory, "a@example.com", role)
    headers = await login_headers(client, user)
    org = uuid.uuid4()
    calls = [
        await client.post(URL, json={"name": "X"}, headers=headers),
        await client.get(URL, headers=headers),
        await client.get(f"{URL}/{org}", headers=headers),
        await client.patch(f"{URL}/{org}", json={"name": "X"}, headers=headers),
    ]
    assert [c.status_code for c in calls] == [403] * 4


async def test_unauthenticated_is_401(client: AsyncClient) -> None:
    org = uuid.uuid4()
    calls = [
        await client.post(URL, json={"name": "X"}),
        await client.get(URL),
        await client.get(f"{URL}/{org}"),
        await client.patch(f"{URL}/{org}", json={"name": "X"}),
    ]
    assert [c.status_code for c in calls] == [401] * 4
    assert calls[0].headers["www-authenticate"] == "Bearer"


async def test_validation_422(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    org = (
        await client.post(URL, json={"name": "Acme"}, headers=headers)
    ).json()
    for create in ({}, {"name": ""}, {"name": "   "}, {"name": "x" * 256}):
        response = await client.post(URL, json=create, headers=headers)
        assert response.status_code == 422
    for body in ({}, {"name": None}, {"is_active": "maybe"}):
        response = await client.patch(
            f"{URL}/{org['id']}", json=body, headers=headers
        )
        assert response.status_code == 422
    assert (await client.get(f"{URL}/nope", headers=headers)).status_code == 422


async def test_pagination(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    for i in range(5):
        await client.post(URL, json={"name": f"Org {i}"}, headers=headers)
    page = (await client.get(f"{URL}?limit=2&offset=2", headers=headers)).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    assert [o["name"] for o in page["items"]] == ["Org 2", "Org 3"]
    for query in ("limit=0", "limit=101", "offset=-1"):
        response = await client.get(f"{URL}?{query}", headers=headers)
        assert response.status_code == 422


async def test_deactivation_ends_users_sessions(
    client: AsyncClient, session_factory: Factory
) -> None:
    headers = await _superadmin(client, session_factory)
    member = await seed_user(session_factory, "m@example.com", Role.STAFF)
    other = await seed_user(session_factory, "o@example.com", Role.STAFF)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": member.email, "password": "correct horse battery"},
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    other_headers = await login_headers(client, other)
    me = f"/api/v1/users/{member.user_id}"
    assert (await client.get(me, headers=member_headers)).status_code == 200

    response = await client.patch(
        f"{URL}/{member.organization_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert response.status_code == 200
    assert (await client.get(me, headers=member_headers)).status_code == 401
    refreshed = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login.json()["refresh_token"]},
    )
    assert refreshed.status_code == 401
    relogin = await client.post(
        "/api/v1/auth/login",
        json={"email": member.email, "password": "correct horse battery"},
    )
    assert relogin.status_code == 401
    async with session_factory() as session:
        tokens = (await session.scalars(select(RefreshToken))).all()
    by_user = {t.user_id: t.revoked_at for t in tokens}
    assert by_user[member.user_id] is not None
    assert by_user[other.user_id] is None  # other organizations untouched
    other_me = f"/api/v1/users/{other.user_id}"
    assert (
        await client.get(other_me, headers=other_headers)
    ).status_code == 200

    # Reactivation lets users log in again (data was kept).
    await client.patch(
        f"{URL}/{member.organization_id}",
        json={"is_active": True},
        headers=headers,
    )
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": member.email, "password": "correct horse battery"},
        )
    ).status_code == 200
