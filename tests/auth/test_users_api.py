"""User management API tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select

from components.auth.models import RefreshToken
from components.core.enums import Role
from components.users.models import User
from tests.auth.conftest import (
    PASSWORD,
    Factory,
    Seeded,
    login_headers,
    seed_user,
)

pytestmark = pytest.mark.integration

URL = "/api/v1/users"
NEW_PASSWORD = "another long password"


@pytest.fixture(autouse=True)
def _api(api: None) -> None:
    return None


class World:
    """Two organizations: admin/staff in A, admin/staff in B, a superadmin."""

    def __init__(self, client: AsyncClient, factory: Factory) -> None:
        self.client = client
        self.factory = factory

    async def build(self) -> "World":
        f = self.factory
        self.root = await seed_user(f, "root@example.com", Role.SUPERADMIN)
        self.admin_a = await seed_user(f, "admin-a@example.com", Role.ADMIN)
        org_a = self.admin_a.organization_id
        self.staff_a = await seed_user(
            f, "staff-a@example.com", Role.STAFF, existing_organization_id=org_a
        )
        self.admin_b = await seed_user(f, "admin-b@example.com", Role.ADMIN)
        org_b = self.admin_b.organization_id
        self.staff_b = await seed_user(
            f, "staff-b@example.com", Role.STAFF, existing_organization_id=org_b
        )
        self.org_a = org_a
        self.org_b = org_b
        return self

    async def h(self, user: Seeded) -> dict[str, str]:
        return await login_headers(self.client, user)


@pytest.fixture
async def world(client: AsyncClient, session_factory: Factory) -> World:
    return await World(client, session_factory).build()


def _create(
    email: str = "new@example.com",
    role: str = "staff",
    organization_id: object = None,
    password: str = PASSWORD,
) -> dict[str, object]:
    body: dict[str, object] = {
        "email": email,
        "role": role,
        "password": password,
    }
    if organization_id is not None:
        body["organization_id"] = str(organization_id)
    return body


def _no_secrets(response: Response) -> None:
    text = response.text.lower()
    assert "argon2" not in text
    assert "hash" not in text


# --- create ------------------------------------------------------------------


async def test_unauthenticated_create_is_401(client: AsyncClient) -> None:
    response = await client.post(URL, json=_create())
    assert response.status_code == 401


async def test_staff_cannot_create(world: World) -> None:
    response = await world.client.post(
        URL, json=_create(), headers=await world.h(world.staff_a)
    )
    assert response.status_code == 403


async def test_admin_creates_staff_in_own_organization(world: World) -> None:
    response = await world.client.post(
        URL, json=_create(), headers=await world.h(world.admin_a)
    )
    assert response.status_code == 201
    body = response.json()
    assert body["organization_id"] == str(world.org_a)
    assert body["role"] == "staff" and body["is_active"] is True
    _no_secrets(response)
    assert set(body) == {
        "id",
        "organization_id",
        "email",
        "role",
        "is_active",
        "created_at",
    }
    # The new user can log in with the given password.
    login = await world.client.post(
        "/api/v1/auth/login",
        json={"email": "new@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200


async def test_admin_creates_admin(world: World) -> None:
    response = await world.client.post(
        URL,
        json=_create(role="admin", organization_id=world.org_a),
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 201 and response.json()["role"] == "admin"


async def test_admin_cannot_create_in_other_organization(world: World) -> None:
    response = await world.client.post(
        URL,
        json=_create(organization_id=world.org_b),
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 403


@pytest.mark.parametrize("org", [None, "own"])
async def test_admin_cannot_create_superadmin(
    world: World, org: str | None
) -> None:
    response = await world.client.post(
        URL,
        json=_create(
            role="superadmin",
            organization_id=world.org_a if org else None,
        ),
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 403


async def test_superadmin_creates_in_any_organization(world: World) -> None:
    response = await world.client.post(
        URL,
        json=_create(role="admin", organization_id=world.org_b),
        headers=await world.h(world.root),
    )
    assert response.status_code == 201
    assert response.json()["organization_id"] == str(world.org_b)


async def test_superadmin_cannot_create_superadmin_over_http(
    world: World,
) -> None:
    response = await world.client.post(
        URL,
        json=_create(role="superadmin"),
        headers=await world.h(world.root),
    )
    assert response.status_code == 403


async def test_role_organization_rule_422(world: World) -> None:
    headers = await world.h(world.root)
    no_org = await world.client.post(URL, json=_create(), headers=headers)
    assert no_org.status_code == 422
    assert no_org.json()["detail"][0]["loc"] == ["body", "organization_id"]
    super_with_org = await world.client.post(
        URL,
        json=_create(role="superadmin", organization_id=world.org_a),
        headers=headers,
    )
    assert super_with_org.status_code == 422
    unknown_org = await world.client.post(
        URL,
        json=_create(organization_id=uuid.uuid4()),
        headers=headers,
    )
    assert unknown_org.status_code == 422


@pytest.mark.parametrize(
    "password", ["short-8ch", "x" * 129, "valid password\x00 with nul"]
)
async def test_password_policy_422_names_field(
    world: World, password: str
) -> None:
    response = await world.client.post(
        URL,
        json=_create(password=password),
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "password"
    assert password not in response.text


async def test_password_boundaries_accepted(world: World) -> None:
    headers = await world.h(world.admin_a)
    for i, password in enumerate(["p" * 12, "p" * 128]):
        response = await world.client.post(
            URL,
            json=_create(f"b{i}@example.com", password=password),
            headers=headers,
        )
        assert response.status_code == 201


@pytest.mark.parametrize(
    "email",
    [
        "not-an-email",
        "a@b",
        "a b@example.com",
        "x" * 250 + "@e.com",
        "",
        "a\x00b@example.com",
        "\u0130@example.com",
        "j\u00fcrgen@example.com",
        "tab\t@example.com",
    ],
)
async def test_invalid_email_422(world: World, email: str) -> None:
    response = await world.client.post(
        URL, json=_create(email), headers=await world.h(world.admin_a)
    )
    assert response.status_code == 422


async def test_max_length_ascii_email_is_accepted(world: World) -> None:
    email = "a" * (254 - len("@example.com")) + "@example.com"
    assert len(email) == 254
    response = await world.client.post(
        URL, json=_create(email), headers=await world.h(world.admin_a)
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "email", ["STAFF-A@example.com", "staff-a@EXAMPLE.com"]
)
async def test_duplicate_email_ignoring_case_409(
    world: World, email: str
) -> None:
    # Even when the existing user is in another organization.
    response = await world.client.post(
        URL, json=_create(email), headers=await world.h(world.admin_b)
    )
    assert response.status_code == 409


# --- read --------------------------------------------------------------------


def _emails(response: Response) -> set[str]:
    return {u["email"] for u in response.json()["items"]}


async def test_admin_lists_own_organization(world: World) -> None:
    headers = await world.h(world.admin_a)
    response = await world.client.get(URL, headers=headers)
    assert response.status_code == 200
    assert _emails(response) == {"admin-a@example.com", "staff-a@example.com"}
    assert response.json()["total"] == 2
    _no_secrets(response)
    # A foreign organization filter cannot widen the scope.
    other = await world.client.get(
        f"{URL}?organization_id={world.org_b}", headers=headers
    )
    assert other.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_staff_lists_only_self(world: World) -> None:
    response = await world.client.get(URL, headers=await world.h(world.staff_a))
    assert _emails(response) == {"staff-a@example.com"}
    assert response.json()["total"] == 1


async def test_superadmin_lists_all_with_filters(world: World) -> None:
    headers = await world.h(world.root)
    everyone = await world.client.get(URL, headers=headers)
    assert everyone.json()["total"] == 5
    by_org = await world.client.get(
        f"{URL}?organization_id={world.org_b}", headers=headers
    )
    assert _emails(by_org) == {"admin-b@example.com", "staff-b@example.com"}
    by_role = await world.client.get(f"{URL}?role=superadmin", headers=headers)
    assert _emails(by_role) == {"root@example.com"}
    await world.client.patch(
        f"{URL}/{world.staff_b.user_id}",
        json={"is_active": False},
        headers=headers,
    )
    inactive = await world.client.get(f"{URL}?is_active=false", headers=headers)
    assert _emails(inactive) == {"staff-b@example.com"}
    combined = await world.client.get(
        f"{URL}?organization_id={world.org_b}&role=admin&is_active=true",
        headers=headers,
    )
    assert _emails(combined) == {"admin-b@example.com"}


async def test_pagination(world: World) -> None:
    headers = await world.h(world.root)
    page = (
        await world.client.get(f"{URL}?limit=2&offset=1", headers=headers)
    ).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    assert page["limit"] == 2 and page["offset"] == 1
    first = (await world.client.get(f"{URL}?limit=2", headers=headers)).json()
    assert first["items"][1] == page["items"][0]
    past = (await world.client.get(f"{URL}?offset=99", headers=headers)).json()
    assert past["items"] == [] and past["total"] == 5
    for query in ("limit=0", "limit=101", "offset=-1", "role=owner"):
        response = await world.client.get(f"{URL}?{query}", headers=headers)
        assert response.status_code == 422


async def test_get_scope(world: World) -> None:
    c = world.client
    admin = await world.h(world.admin_a)
    ok = await c.get(f"{URL}/{world.staff_a.user_id}", headers=admin)
    assert ok.status_code == 200 and ok.json()["email"] == "staff-a@example.com"
    _no_secrets(ok)
    # Other organization and superadmin are indistinguishable from missing.
    for target in (world.staff_b, world.root):
        r = await c.get(f"{URL}/{target.user_id}", headers=admin)
        assert r.status_code == 404
    missing = await c.get(f"{URL}/{uuid.uuid4()}", headers=admin)
    assert missing.status_code == 404
    staff = await world.h(world.staff_a)
    assert (
        await c.get(f"{URL}/{world.staff_a.user_id}", headers=staff)
    ).status_code == 200
    assert (
        await c.get(f"{URL}/{world.admin_a.user_id}", headers=staff)
    ).status_code == 404
    root = await world.h(world.root)
    assert (
        await c.get(f"{URL}/{world.staff_b.user_id}", headers=root)
    ).status_code == 200
    assert (
        await c.get(f"{URL}/{world.root.user_id}", headers=root)
    ).status_code == 200


async def test_unauthenticated_reads_and_updates_401(
    client: AsyncClient,
) -> None:
    uid = uuid.uuid4()
    calls = [
        await client.get(URL),
        await client.get(f"{URL}/{uid}"),
        await client.patch(f"{URL}/{uid}", json={"is_active": False}),
    ]
    assert [c.status_code for c in calls] == [401] * 3


# --- update ------------------------------------------------------------------


async def test_staff_cannot_update(world: World) -> None:
    response = await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json={"password": NEW_PASSWORD},
        headers=await world.h(world.staff_a),
    )
    assert response.status_code == 403


async def test_admin_updates_role_and_active(world: World) -> None:
    headers = await world.h(world.admin_a)
    response = await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json={"role": "admin", "is_active": False},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["is_active"] is False
    _no_secrets(response)


async def test_admin_cannot_update_other_organization(world: World) -> None:
    response = await world.client.patch(
        f"{URL}/{world.staff_b.user_id}",
        json={"is_active": False},
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 404


async def test_nobody_edits_superadmin(world: World) -> None:
    body = {"is_active": False}
    by_admin = await world.client.patch(
        f"{URL}/{world.root.user_id}",
        json=body,
        headers=await world.h(world.admin_a),
    )
    assert by_admin.status_code == 404
    by_root = await world.client.patch(
        f"{URL}/{world.root.user_id}",
        json={"password": NEW_PASSWORD},
        headers=await world.h(world.root),
    )
    assert by_root.status_code == 403


async def test_superadmin_updates_any_non_superadmin(world: World) -> None:
    response = await world.client.patch(
        f"{URL}/{world.staff_b.user_id}",
        json={"role": "admin"},
        headers=await world.h(world.root),
    )
    assert response.status_code == 200 and response.json()["role"] == "admin"


async def test_cannot_set_superadmin_role(world: World) -> None:
    response = await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json={"role": "superadmin"},
        headers=await world.h(world.root),
    )
    assert response.status_code == 422


async def test_self_lockout_409(world: World) -> None:
    headers = await world.h(world.admin_a)
    me = f"{URL}/{world.admin_a.user_id}"
    deactivate = await world.client.patch(
        me, json={"is_active": False}, headers=headers
    )
    assert deactivate.status_code == 409
    demote = await world.client.patch(
        me, json={"role": "staff"}, headers=headers
    )
    assert demote.status_code == 409
    # Nothing changed, and a no-op role or own password change is fine.
    assert (await world.client.get(me, headers=headers)).json()[
        "role"
    ] == "admin"
    ok = await world.client.patch(
        me, json={"role": "admin", "is_active": True}, headers=headers
    )
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "body", [{}, {"role": None}, {"is_active": "perhaps"}, {"role": "boss"}]
)
async def test_update_validation_422(
    world: World, body: dict[str, object]
) -> None:
    response = await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json=body,
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 422


async def test_update_weak_password_422(world: World) -> None:
    response = await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json={"password": "short"},
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "password"


async def _staff_session(world: World) -> tuple[dict[str, str], str]:
    login = await world.client.post(
        "/api/v1/auth/login",
        json={"email": world.staff_a.email, "password": PASSWORD},
    )
    body = login.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body[
        "refresh_token"
    ]


async def test_deactivation_ends_sessions(world: World) -> None:
    access, refresh = await _staff_session(world)
    me = f"{URL}/{world.staff_a.user_id}"
    assert (await world.client.get(me, headers=access)).status_code == 200
    response = await world.client.patch(
        me, json={"is_active": False}, headers=await world.h(world.admin_a)
    )
    assert response.status_code == 200
    assert (await world.client.get(me, headers=access)).status_code == 401
    refreshed = await world.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh}
    )
    assert refreshed.status_code == 401
    async with world.factory() as session:
        revoked = await session.scalars(
            select(RefreshToken.revoked_at).where(
                RefreshToken.user_id == world.staff_a.user_id
            )
        )
        assert all(r is not None for r in revoked)


async def test_password_change_ends_sessions_and_swaps_password(
    world: World,
) -> None:
    access, refresh = await _staff_session(world)
    response = await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json={"password": NEW_PASSWORD},
        headers=await world.h(world.admin_a),
    )
    assert response.status_code == 200
    assert NEW_PASSWORD not in response.text
    refreshed = await world.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh}
    )
    assert refreshed.status_code == 401
    old = await world.client.post(
        "/api/v1/auth/login",
        json={"email": world.staff_a.email, "password": PASSWORD},
    )
    assert old.status_code == 401
    new = await world.client.post(
        "/api/v1/auth/login",
        json={"email": world.staff_a.email, "password": NEW_PASSWORD},
    )
    assert new.status_code == 200
    async with world.factory() as session:
        stored = await session.scalar(
            select(User.password_hash).where(User.id == world.staff_a.user_id)
        )
    assert stored is not None and stored.startswith("$argon2id$")
    assert NEW_PASSWORD not in stored


async def test_role_change_applies_immediately(world: World) -> None:
    access, _ = await _staff_session(world)
    assert (await world.client.get(URL, headers=access)).json()["total"] == 1
    await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json={"role": "admin"},
        headers=await world.h(world.admin_a),
    )
    assert (await world.client.get(URL, headers=access)).json()["total"] == 2


async def test_unaffected_users_keep_sessions(world: World) -> None:
    other = await world.h(world.staff_b)
    await world.client.patch(
        f"{URL}/{world.staff_a.user_id}",
        json={"is_active": False},
        headers=await world.h(world.admin_a),
    )
    me = f"{URL}/{world.staff_b.user_id}"
    assert (await world.client.get(me, headers=other)).status_code == 200
