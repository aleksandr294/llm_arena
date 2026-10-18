"""Blocking organizations from the panel (superadmin only)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from components.auth.models import RefreshToken
from components.organizations.models import Organization
from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import API, Panel

pytestmark = pytest.mark.integration


def _change(
    panel: Panel, client: str, org: uuid.UUID, body: object
) -> tuple[int, str]:
    reply = panel.request(
        client, "PATCH", f"{API}/change/Organization/{org}", json_body=body
    )
    return reply.status, reply.text


def _rest_login(panel: Panel, email: str) -> tuple[int, dict[str, str]]:
    reply = panel.request(
        "rest",
        "POST",
        "/api/v1/auth/login",
        json_body={"email": email, "password": PASSWORD},
    )
    return reply.status, reply.json() if reply.status == 200 else {}


def _rest_refresh(panel: Panel, refresh_token: str) -> int:
    return panel.request(
        "rest",
        "POST",
        "/api/v1/auth/refresh",
        json_body={"refresh_token": refresh_token},
    ).status


async def _is_active(engine: AsyncEngine, org: uuid.UUID) -> bool | None:
    async with engine.connect() as conn:
        return await conn.scalar(
            select(Organization.is_active).where(Organization.id == org)
        )


async def test_superadmin_blocks_and_unblocks_an_organization(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    org = world.org_ids["a"]
    status, tokens = _rest_login(panel, emails["staff_a"])
    assert status == 200
    assert panel.sign_in("root", emails["superadmin"], PASSWORD).status == 200

    status, _ = _change(panel, "root", org, {"is_active": False})
    assert status == 200
    assert await _is_active(engine, org) is False
    # Same effect as the REST deactivation: no login, no refresh.
    assert _rest_login(panel, emails["staff_a"])[0] == 401
    assert _rest_refresh(panel, tokens["refresh_token"]) == 401
    # Another organization is untouched.
    assert _rest_login(panel, emails["staff_c"])[0] == 200

    status, text = _change(panel, "root", org, {"is_active": True})
    assert status == 200
    assert await _is_active(engine, org) is True
    assert _rest_login(panel, emails["staff_a"])[0] == 200
    # The refresh tokens revoked by the block stay revoked.
    assert _rest_refresh(panel, tokens["refresh_token"]) == 401
    detail = panel.get("root", f"/retrieve/Organization/{org}").json()
    assert detail["is_active"] is True


async def test_blocking_revokes_refresh_tokens_in_the_same_change(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    for name in ("staff_a", "admin_a", "staff_c"):
        assert _rest_login(panel, emails[name])[0] == 200
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    assert (
        _change(panel, "root", world.org_ids["a"], {"is_active": False})[0]
        == 200
    )

    ids_of_a = {
        world.principals[n].user_id for n in ("staff_a", "staff_b", "admin_a")
    }
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(RefreshToken.user_id, RefreshToken.revoked_at)
            )
        ).all()
    assert rows
    for user_id, revoked_at in rows:
        assert (revoked_at is not None) == (user_id in ids_of_a)


async def test_only_is_active_is_taken_from_the_payload(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    org = world.org_ids["a"]
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    async with engine.connect() as conn:
        before = (
            await conn.execute(select(Organization).order_by(Organization.id))
        ).all()

    status, _ = _change(
        panel,
        "root",
        org,
        {
            "id": str(world.org_ids["b"]),
            "name": "hijacked",
            "name_lower": "hijacked",
            "created_at": "2001-01-01 00:00",
            "is_active": False,
        },
    )
    assert status == 200
    status, _ = _change(panel, "root", org, {"name": "renamed"})
    assert status == 200  # nothing to apply is not an error

    async with engine.connect() as conn:
        after = (
            await conn.execute(select(Organization).order_by(Organization.id))
        ).all()
    assert len(before) == len(after) == 2
    for old, new in zip(before, after, strict=True):
        assert (old.id, old.name, old.name_lower, old.created_at) == (
            new.id,
            new.name,
            new.name_lower,
            new.created_at,
        )
        assert new.is_active == (old.is_active and new.id != org)


async def test_bad_organization_changes_are_rejected(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    org = world.org_ids["a"]
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    for value in ("false", "", 0, None, [False], {"x": 1}):
        status, _ = _change(panel, "root", org, {"is_active": value})
        assert status == 422, value
        assert await _is_active(engine, org) is True, value
    assert _change(panel, "root", uuid.uuid4(), {"is_active": False})[0] == 404
    status, _ = _change(panel, "root", "not-a-uuid", {"is_active": False})  # type: ignore[arg-type]
    assert status == 404


@pytest.mark.parametrize("actor", ["staff_a", "staff_b", "admin_a", "admin_b"])
async def test_staff_and_admins_cannot_change_organizations(
    panel: Panel,
    engine: AsyncEngine,
    world: World,
    emails: dict[str, str],
    actor: str,
) -> None:
    panel.sign_in("c", emails[actor], PASSWORD)
    for org in world.org_ids.values():
        assert _change(panel, "c", org, {"is_active": False})[0] == 403
        assert await _is_active(engine, org) is True
        assert panel.get("c", f"/retrieve/Organization/{org}").status == 404
    assert panel.get("c", "/list/Organization").json()["results"] == []


async def test_organizations_cannot_be_added_deleted_or_exported(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    org = world.org_ids["a"]
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    calls: list[tuple[str, str, Any]] = [
        ("POST", f"{API}/add/Organization", {"name": "new"}),
        ("DELETE", f"{API}/delete/Organization/{org}", None),
        ("POST", f"{API}/export/Organization", {"format": "CSV"}),
        ("POST", f"{API}/action/Organization/x", {"ids": []}),
    ]
    for method, path, body in calls:
        assert panel.request("root", method, path, json_body=body).status == 403
    config = panel.get("root", "/configuration").json()["models"]
    organization = next(m for m in config if m["name"] == "Organization")
    assert organization["permissions"] == ["Change"]


async def test_panel_session_ends_when_the_organization_is_blocked(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    org = world.org_ids["a"]
    for name in ("staff_a", "admin_a"):
        assert panel.sign_in(name, emails[name], PASSWORD).status == 200
        assert panel.get(name, "/list/Dataset").status == 200
    assert panel.sign_in("root", emails["superadmin"], PASSWORD).status == 200
    assert panel.sign_in("other", emails["admin_b"], PASSWORD).status == 200

    assert _change(panel, "root", org, {"is_active": False})[0] == 200
    for name in ("staff_a", "admin_a"):
        assert panel.get(name, "/list/Dataset").status == 401
        assert panel.get(name, "/me").status == 401
        assert panel.sign_in(name, emails[name], PASSWORD).status == 401
    # Other organizations and the superadmin keep working.
    assert panel.get("other", "/list/Dataset").status == 200
    assert panel.get("root", "/list/Dataset").status == 200

    assert _change(panel, "root", org, {"is_active": True})[0] == 200
    assert panel.sign_in("staff_a", emails["staff_a"], PASSWORD).status == 200
