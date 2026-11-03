"""Panel write operations: users, organizations, refresh tokens and
experiment deletion, over fastadmin's real HTTP API."""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from components.auth.models import RefreshToken
from components.datasets.models import Dataset, TestCase
from components.evaluation.models import EvaluationResult
from components.experiments.models import Experiment, ExperimentResult
from components.organizations.models import Organization
from components.users.models import User
from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import API, Panel
from tests.security.helpers import CORPUS, MARK

pytestmark = pytest.mark.integration

NEW_PASSWORD = "an initial password 1"


def _add(panel: Panel, client: str, model: str, body: object) -> Any:
    return panel.request(client, "POST", f"{API}/add/{model}", json_body=body)


def _change(
    panel: Panel, client: str, model: str, row: uuid.UUID, body: object
) -> Any:
    return panel.request(
        client, "PATCH", f"{API}/change/{model}/{row}", json_body=body
    )


def _login(panel: Panel, email: str, password: str = PASSWORD) -> Any:
    return panel.request(
        "rest",
        "POST",
        "/api/v1/auth/login",
        json_body={"email": email, "password": password},
    )


def _refresh(panel: Panel, token: str) -> int:
    return panel.request(
        "rest",
        "POST",
        "/api/v1/auth/refresh",
        json_body={"refresh_token": token},
    ).status


async def _user_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int(
            await conn.scalar(select(func.count()).select_from(User)) or 0
        )


async def _user(engine: AsyncEngine, email: str) -> Any:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(User).where(User.email_lower == email.lower())
            )
        ).first()
    return row


def _body(email: str, **extra: Any) -> dict[str, Any]:
    return {
        "email": email,
        "password": NEW_PASSWORD,
        "role": "staff",
        **extra,
    }


# --- users: create -----------------------------------------------------------


async def test_admin_creates_a_staff_user_in_their_organization(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("c", emails["admin_a"], PASSWORD)
    reply = _add(panel, "c", "User", _body("New.Staff@example.com"))
    assert reply.status == 200, reply.text
    assert NEW_PASSWORD not in reply.text
    assert "password" not in reply.json()
    assert "password_hash" not in reply.json()
    user = await _user(engine, "new.staff@example.com")
    assert user is not None
    assert user.organization_id == world.org_ids["a"]
    assert user.role == "staff"
    assert user.is_active is True
    assert _login(panel, "New.Staff@example.com", NEW_PASSWORD).status == 200


async def test_superadmin_creates_an_admin_in_a_named_organization(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    missing = _add(panel, "root", "User", _body("a1@example.com", role="admin"))
    assert missing.status == 422
    reply = _add(
        panel,
        "root",
        "User",
        _body(
            "a2@example.com",
            role="admin",
            organization_id=str(world.org_ids["b"]),
        ),
    )
    assert reply.status == 200, reply.text
    user = await _user(engine, "a2@example.com")
    assert user is not None
    assert (user.role, user.organization_id) == ("admin", world.org_ids["b"])


async def test_staff_cannot_create_users_or_organizations(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    before = await _user_count(engine)
    panel.sign_in("c", emails["staff_a"], PASSWORD)
    assert _add(panel, "c", "User", _body("s@example.com")).status == 403
    assert _add(panel, "c", "Organization", {"name": "n"}).status == 403
    assert await _user_count(engine) == before


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (_body("STAFF-A-DUP@example.com"), 409),
        (_body("ok@example.com", password="short"), 422),
        (_body("ok@example.com", role="superadmin"), 403),
        (_body("not-an-email"), 422),
        (_body("ok@example.com", role="nonsense"), 422),
        (_body("ok@example.com", password=123), 422),
    ],
)
async def test_rejected_user_creations_create_nothing_and_hide_the_password(
    panel: Panel,
    engine: AsyncEngine,
    world: World,
    emails: dict[str, str],
    body: dict[str, Any],
    status: int,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            update(User)
            .where(User.id == world.principals["staff_a"].user_id)
            .values(
                email="staff-a-dup@example.com",
                email_lower="staff-a-dup@example.com",
            )
        )
    before = await _user_count(engine)
    panel.sign_in("c", emails["admin_a"], PASSWORD)
    reply = _add(panel, "c", "User", body)
    assert reply.status == status, reply.text
    assert NEW_PASSWORD not in reply.text
    assert "short" not in reply.text
    assert await _user_count(engine) == before


async def test_admin_cannot_name_another_organization(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    before = await _user_count(engine)
    panel.sign_in("c", emails["admin_a"], PASSWORD)
    reply = _add(
        panel,
        "c",
        "User",
        _body("x@example.com", organization_id=str(world.org_ids["b"])),
    )
    assert reply.status == 403
    assert await _user_count(engine) == before


async def test_crafted_extra_fields_on_create_are_ignored(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("c", emails["admin_a"], PASSWORD)
    forced = uuid.uuid4()
    reply = _add(
        panel,
        "c",
        "User",
        _body(
            "crafted@example.com",
            id=str(forced),
            is_active=False,
            password_hash="plain-text-hash",
            email_lower="other@example.com",
            created_at="2000-01-01T00:00:00Z",
        ),
    )
    assert reply.status == 200, reply.text
    user = await _user(engine, "crafted@example.com")
    assert user is not None
    assert user.id != forced
    assert user.is_active is True
    assert user.password_hash != "plain-text-hash"
    assert user.email_lower == "crafted@example.com"
    assert _login(panel, "crafted@example.com", NEW_PASSWORD).status == 200


async def test_password_input_is_never_serialized_filtered_or_sorted(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    config = panel.get("root", "/configuration").json()
    user_model = next(m for m in config["models"] if m["name"] == "User")
    fields = {f["name"]: f for f in user_model["fields"]}
    assert "password_hash" not in fields
    assert fields["password"]["add_configuration"]["form_widget_type"] == (
        "PasswordInput"
    )
    listing = panel.get("root", "/list/User", limit=100)
    detail = panel.get(
        "root", f"/retrieve/User/{world.principals['staff_a'].user_id}"
    )
    for reply in (listing, detail):
        assert reply.status == 200
        assert "password" not in reply.text
        assert "$argon2" not in reply.text
    for sort in ("password", "-password", "organization_id"):
        assert panel.get("root", "/list/User", sort_by=sort).status == 422
    assert (
        panel.get("root", "/list/User", **{"password__exact": "a"}).status
        == 422
    )
    assert (
        panel.get("root", "/list/User", **{"password__contains": "a"}).status
        != 200
    )


@pytest.mark.parametrize(
    ("model", "key"),
    [
        ("User", "organization_name"),
        ("User", "active_refresh_tokens"),
        ("Organization", "user_count"),
        ("Dataset", "owner_email"),
        ("Experiment", "dataset_name"),
        ("Experiment", "results_by_status"),
        ("ExperimentResult", "experiment_name"),
    ],
)
async def test_computed_keys_are_refused_as_filter_or_sort(
    panel: Panel, world: World, emails: dict[str, str], model: str, key: str
) -> None:
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    assert panel.get("root", f"/list/{model}").status == 200
    for params in (
        {"sort_by": key},
        {"sort_by": f"-{key}"},
        {f"{key}__exact": "x"},
        {f"{key}__contains": "x"},
    ):
        reply = panel.get("root", f"/list/{model}", **params)
        assert reply.status == 422, (model, params, reply.status)


# --- users: block ------------------------------------------------------------


async def test_admin_blocks_and_unblocks_a_staff_user(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    target = world.principals["staff_a"].user_id
    login = _login(panel, emails["staff_a"])
    assert login.status == 200
    refresh = login.json()["refresh_token"]
    assert panel.sign_in("staff", emails["staff_a"], PASSWORD).status == 200
    panel.sign_in("c", emails["admin_a"], PASSWORD)

    assert (
        _change(panel, "c", "User", target, {"is_active": False}).status == 200
    )
    assert _login(panel, emails["staff_a"]).status == 401
    assert _refresh(panel, refresh) == 401
    assert panel.get("staff", "/me").status == 401
    assert panel.sign_in("again", emails["staff_a"], PASSWORD).status == 401

    assert (
        _change(panel, "c", "User", target, {"is_active": True}).status == 200
    )
    assert _login(panel, emails["staff_a"]).status == 200


async def test_only_is_active_is_applied_on_user_change(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    target = world.principals["staff_a"].user_id
    panel.sign_in("c", emails["admin_a"], PASSWORD)
    reply = _change(
        panel,
        "c",
        "User",
        target,
        {
            "role": "admin",
            "email": "changed@example.com",
            "password": "brand new password",
            "password_hash": "x",
            "organization_id": str(world.org_ids["b"]),
            "is_active": False,
        },
    )
    assert reply.status == 200, reply.text
    async with engine.connect() as conn:
        row = (await conn.execute(select(User).where(User.id == target))).one()
    assert row.role == "staff"
    assert row.email == emails["staff_a"]
    assert row.organization_id == world.org_ids["a"]
    assert row.is_active is False
    assert row.password_hash != "x"
    assert _login(panel, emails["staff_a"]).status == 401  # blocked
    assert (
        _change(panel, "c", "User", target, {"is_active": "no"}).status == 422
    )


async def test_forbidden_block_targets_stay_active(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("a", emails["admin_a"], PASSWORD)
    panel.sign_in("s", emails["staff_a"], PASSWORD)
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    body = {"is_active": False}
    ids = {n: p.user_id for n, p in world.principals.items()}

    results = {
        "admin_blocks_superadmin": _change(
            panel, "a", "User", ids["superadmin"], body
        ).status,
        "admin_blocks_self": _change(
            panel, "a", "User", ids["admin_a"], body
        ).status,
        "admin_blocks_other_org": _change(
            panel, "a", "User", ids["staff_c"], body
        ).status,
        "staff_blocks_other": _change(
            panel, "s", "User", ids["staff_b"], body
        ).status,
        "superadmin_blocks_self": _change(
            panel, "root", "User", ids["superadmin"], body
        ).status,
    }
    assert results == {
        "admin_blocks_superadmin": 404,
        "admin_blocks_self": 409,
        "admin_blocks_other_org": 404,
        "staff_blocks_other": 403,
        "superadmin_blocks_self": 403,
    }
    async with engine.connect() as conn:
        inactive = await conn.scalar(
            select(func.count()).where(User.is_active.is_(False))
        )
    assert inactive == 0


# --- organizations -----------------------------------------------------------


async def test_superadmin_creates_an_organization(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    reply = _add(
        panel,
        "root",
        "Organization",
        {"name": "  Fresh Org  ", "is_active": False},
    )
    assert reply.status == 200, reply.text
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(Organization).where(Organization.name == "Fresh Org")
            )
        ).one()
    assert row.is_active is True
    assert row.name_lower == "fresh org"
    # Duplicate, ignoring case.
    dup = _add(panel, "root", "Organization", {"name": "FRESH ORG"})
    assert dup.status == 409
    for bad in ({"name": ""}, {"name": 5}, {}, {"name": "x" * 300}):
        assert _add(panel, "root", "Organization", bad).status == 422


async def test_non_superadmins_cannot_create_organizations(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    for name in ("admin_a", "staff_a"):
        panel.sign_in(name, emails[name], PASSWORD)
        reply = _add(panel, name, "Organization", {"name": "Sneaky"})
        assert reply.status == 403
    async with engine.connect() as conn:
        assert (
            await conn.scalar(
                select(func.count())
                .select_from(Organization)
                .where(Organization.name == "Sneaky")
            )
        ) == 0


async def test_organization_edit_still_changes_only_is_active(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    org = world.org_ids["a"]
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    async with engine.connect() as conn:
        before = (
            await conn.execute(
                select(Organization.name, Organization.name_lower).where(
                    Organization.id == org
                )
            )
        ).one()
    reply = _change(
        panel,
        "root",
        "Organization",
        org,
        {"name": "Renamed", "is_active": False},
    )
    assert reply.status == 200, reply.text
    async with engine.connect() as conn:
        after = (
            await conn.execute(
                select(
                    Organization.name,
                    Organization.name_lower,
                    Organization.is_active,
                ).where(Organization.id == org)
            )
        ).one()
    assert (after.name, after.name_lower) == tuple(before)
    assert after.is_active is False


# --- refresh tokens ----------------------------------------------------------


async def test_admin_revokes_refresh_tokens_of_own_organization(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    mine = _login(panel, emails["staff_a"]).json()["refresh_token"]
    other = _login(panel, emails["staff_c"]).json()["refresh_token"]
    panel.sign_in("c", emails["admin_a"], PASSWORD)
    ids = [
        str(world.principals["staff_a"].user_id),
        str(world.principals["staff_c"].user_id),
        "not-a-uuid",
        str(uuid.uuid4()),
    ]
    reply = panel.request(
        "c",
        "POST",
        f"{API}/action/User/revoke_refresh_tokens",
        json_body={"ids": ids},
    )
    assert reply.status == 200, reply.text
    # Same answer whatever was selected: no per-id information.
    empty = panel.request(
        "c",
        "POST",
        f"{API}/action/User/revoke_refresh_tokens",
        json_body={"ids": []},
    )
    assert empty.text == reply.text
    assert _refresh(panel, mine) == 401
    assert _refresh(panel, other) == 200  # other organization untouched
    async with engine.connect() as conn:
        active = await conn.scalar(
            select(User.is_active).where(
                User.id == world.principals["staff_a"].user_id
            )
        )
    assert active is True
    assert _login(panel, emails["staff_a"]).status == 200


async def test_superadmin_revokes_any_user_and_staff_cannot(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    token = _login(panel, emails["staff_c"]).json()["refresh_token"]
    url = f"{API}/action/User/revoke_refresh_tokens"
    target = [str(world.principals["staff_c"].user_id)]
    panel.sign_in("s", emails["staff_a"], PASSWORD)
    assert (
        panel.request("s", "POST", url, json_body={"ids": target}).status == 403
    )
    assert _refresh(panel, token) == 200
    token = _login(panel, emails["staff_c"]).json()["refresh_token"]
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    assert (
        panel.request("root", "POST", url, json_body={"ids": target}).status
        == 200
    )
    assert _refresh(panel, token) == 401
    too_many = [str(uuid.uuid4()) for _ in range(501)]
    assert (
        panel.request("root", "POST", url, json_body={"ids": too_many}).status
        == 422
    )
    async with engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(RefreshToken))


# --- experiments -------------------------------------------------------------


async def _counts(engine: AsyncEngine, name: uuid.UUID) -> tuple[int, int, int]:
    async with engine.connect() as conn:
        return (
            int(
                await conn.scalar(
                    select(func.count())
                    .select_from(Experiment)
                    .where(Experiment.id == name)
                )
                or 0
            ),
            int(
                await conn.scalar(
                    select(func.count())
                    .select_from(ExperimentResult)
                    .where(ExperimentResult.experiment_id == name)
                )
                or 0
            ),
            int(
                await conn.scalar(
                    select(func.count())
                    .select_from(EvaluationResult)
                    .where(EvaluationResult.experiment_id == name)
                )
                or 0
            ),
        )


async def _set_status(
    engine: AsyncEngine, experiment: uuid.UUID, status: str
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            update(Experiment)
            .where(Experiment.id == experiment)
            .values(status=status)
        )


@pytest.mark.parametrize(
    "status", ["completed", "failed", "partially_failed", "cancelled"]
)
async def test_admin_deletes_a_finished_experiment(
    panel: Panel,
    engine: AsyncEngine,
    world: World,
    emails: dict[str, str],
    status: str,
) -> None:
    exp = world.experiments["exp_a"]
    await _set_status(engine, exp, status)
    panel.sign_in("c", emails["admin_a"], PASSWORD)
    reply = panel.request("c", "DELETE", f"{API}/delete/Experiment/{exp}")
    assert reply.status == 200, reply.text
    assert await _counts(engine, exp) == (0, 0, 0)
    async with engine.connect() as conn:
        assert await conn.scalar(
            select(func.count())
            .select_from(Dataset)
            .where(Dataset.id == world.datasets["ds_a"])
        )
        assert await conn.scalar(
            select(func.count())
            .select_from(TestCase)
            .where(TestCase.id == world.cases["ds_a"])
        )
    # Another experiment is untouched.
    assert (await _counts(engine, world.experiments["exp_b"]))[0] == 1


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_active_experiments_are_refused_with_409(
    panel: Panel,
    engine: AsyncEngine,
    world: World,
    emails: dict[str, str],
    status: str,
) -> None:
    exp = world.experiments["exp_a"]
    await _set_status(engine, exp, status)
    for name in ("admin_a", "superadmin"):
        panel.sign_in(name, emails[name], PASSWORD)
        reply = panel.request(name, "DELETE", f"{API}/delete/Experiment/{exp}")
        assert reply.status == 409, reply.text
        assert "Cancel" in reply.text
    assert await _counts(engine, exp) == (1, 1, 1)


async def test_experiment_delete_scope_and_roles(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    for name in ("exp_a", "exp_c"):
        await _set_status(engine, world.experiments[name], "completed")
    own = world.experiments["exp_a"]
    foreign = world.experiments["exp_c"]

    panel.sign_in("s", emails["staff_a"], PASSWORD)
    assert (
        panel.request("s", "DELETE", f"{API}/delete/Experiment/{own}").status
        == 403
    )
    panel.sign_in("a", emails["admin_a"], PASSWORD)
    assert (
        panel.request(
            "a", "DELETE", f"{API}/delete/Experiment/{foreign}"
        ).status
        == 404
    )
    assert (
        panel.request(
            "a", "DELETE", f"{API}/delete/Experiment/not-a-uuid"
        ).status
        == 404
    )
    assert (await _counts(engine, own))[0] == 1
    assert (await _counts(engine, foreign))[0] == 1

    panel.sign_in("root", emails["superadmin"], PASSWORD)
    assert (
        panel.request(
            "root", "DELETE", f"{API}/delete/Experiment/{foreign}"
        ).status
        == 200
    )
    assert await _counts(engine, foreign) == (0, 0, 0)


async def test_crafted_deletes_of_other_models_stay_forbidden(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    rows = {
        "Dataset": world.datasets["ds_a"],
        "TestCase": world.cases["ds_a"],
        "ExperimentResult": world.results["exp_a"],
        "EvaluationResult": world.evaluations["exp_a"],
        "User": world.principals["staff_a"].user_id,
        "Organization": world.org_ids["a"],
    }
    for model, row in rows.items():
        reply = panel.request("root", "DELETE", f"{API}/delete/{model}/{row}")
        assert reply.status == 403, model
    async with engine.connect() as conn:
        assert await conn.scalar(
            select(func.count())
            .select_from(Dataset)
            .where(Dataset.id == world.datasets["ds_a"])
        )


# --- hostile input and logs --------------------------------------------------


async def test_hostile_input_in_the_new_write_sinks(
    engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    """Emails, organization names and passwords from the hostile corpus:
    controlled answers, no 500, no hostile text in error bodies, nothing
    sensitive in the DEBUG logs."""
    panel = Panel(debug_logs=True)
    secrets: list[str] = []
    try:
        panel.sign_in("root", emails["superadmin"], PASSWORD)
        panel.sign_in("a", emails["admin_a"], PASSWORD)
        for index, sample in enumerate(CORPUS.values()):
            bad_email = _add(panel, "a", "User", _body(sample))
            assert bad_email.status == 422, (sample, bad_email.text)
            assert MARK not in bad_email.text

            org = _add(panel, "root", "Organization", {"name": sample})
            assert org.status in (200, 409, 422), org.text
            if org.status != 200:
                assert MARK not in org.text

            email = f"hostile{index}@example.com"
            password = sample if len(sample) >= 8 else sample * 8
            created = _add(panel, "a", "User", _body(email, password=password))
            assert created.status in (200, 422), created.text
            assert password not in created.text
            secrets.append(password)
            if created.status == 200:
                assert _login(panel, email, password).status == 200
        weak = _add(panel, "a", "User", _body("w@example.com", password="x"))
        assert weak.status == 422
    finally:
        logs = panel.close()
    assert NEW_PASSWORD not in logs
    for secret in secrets:
        assert secret not in logs
    assert MARK not in logs
