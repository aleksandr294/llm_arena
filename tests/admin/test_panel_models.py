"""The panel's models over fastadmin's real HTTP API.

The panel runs in a child process (``Panel``) on the test database. Rows
and flags are changed directly in the database between requests.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from components.organizations.models import Organization
from components.users.models import User
from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import API, Panel
from tests.admin.test_core import (
    DATASETS,
    EXPERIMENTS,
    ORGANIZATIONS,
    USERS,
)

pytestmark = pytest.mark.integration

ACTORS = list(DATASETS)
READ_ONLY_MODELS = (
    "Dataset",
    "TestCase",
    "Experiment",
    "ExperimentResult",
    "EvaluationResult",
    "User",
)
GENERIC_FAILURE = (401, '{"detail":"Invalid credentials."}')
SESSION_COOKIE = "admin_session_id"


def _ids(panel: Panel, client: str, model: str) -> set[str]:
    reply = panel.get(client, f"/list/{model}", limit=1000)
    assert reply.status == 200, reply.text
    return {row["id"] for row in reply.json()["results"]}


def _names(ids: dict[str, uuid.UUID], names: set[str]) -> set[str]:
    return {str(ids[name]) for name in names}


def test_sign_in_as_every_role(panel: Panel, emails: dict[str, str]) -> None:
    for name in ("staff_a", "admin_a", "superadmin"):
        reply = panel.sign_in(name, emails[name], PASSWORD)
        assert reply.status == 200
        assert SESSION_COOKIE in reply.cookies
        # The session cookie must not be readable by scripts.
        assert "httponly" in reply.set_cookie[0].lower()
        assert "samesite=lax" in reply.set_cookie[0].lower()
        me = panel.get(name, "/me")
        assert me.status == 200
        assert me.json()["email"] == emails[name]


def test_sign_in_ignores_email_case_and_padding(
    panel: Panel, emails: dict[str, str]
) -> None:
    reply = panel.sign_in("c", f"  {emails['staff_a'].upper()} ", PASSWORD)
    assert reply.status == 200


async def test_every_sign_in_failure_looks_the_same(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    replies = {
        "wrong password": panel.sign_in("a", emails["staff_a"], "x" * 14),
        "unknown email": panel.sign_in("b", "nobody@example.com", PASSWORD),
        "too long": panel.sign_in("b2", emails["staff_a"], "x" * 500),
    }
    async with engine.begin() as conn:
        await conn.execute(
            update(User)
            .where(User.id == world.principals["staff_b"].user_id)
            .values(is_active=False)
        )
        await conn.execute(
            update(Organization)
            .where(Organization.id == world.org_ids["b"])
            .values(is_active=False)
        )
    replies["inactive user"] = panel.sign_in("c", emails["staff_b"], PASSWORD)
    replies["inactive organization"] = panel.sign_in(
        "d", emails["staff_c"], PASSWORD
    )
    replies["inactive organization admin"] = panel.sign_in(
        "e", emails["admin_b"], PASSWORD
    )

    for label, reply in replies.items():
        assert (reply.status, reply.text) == GENERIC_FAILURE, label
        assert reply.cookies == [], label
        assert reply.set_cookie == [], label
    # The cause is also not visible later: nothing is signed in.
    assert panel.get("c", "/me").status == 401


async def test_session_ends_when_the_user_is_deactivated(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    assert panel.sign_in("s", emails["staff_a"], PASSWORD).status == 200
    assert panel.get("s", "/list/Dataset").status == 200

    async with engine.begin() as conn:
        await conn.execute(
            update(User)
            .where(User.id == world.principals["staff_a"].user_id)
            .values(is_active=False)
        )
    for path in ("/me", "/list/Dataset", "/list/User"):
        assert panel.get("s", path).status == 401, path
    models = panel.get("s", "/configuration").json()["models"]
    assert models == []


async def test_session_ends_when_the_organization_is_deactivated(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    assert panel.sign_in("s", emails["admin_a"], PASSWORD).status == 200
    assert panel.get("s", "/list/Experiment").status == 200

    async with engine.begin() as conn:
        await conn.execute(
            update(Organization)
            .where(Organization.id == world.org_ids["a"])
            .values(is_active=False)
        )
    assert panel.get("s", "/me").status == 401
    assert panel.get("s", "/list/Experiment").status == 401


async def test_a_deleted_role_cannot_keep_a_session(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    """A superadmin keeps working while organizations are inactive: they
    belong to none."""
    async with engine.begin() as conn:
        await conn.execute(update(Organization).values(is_active=False))
    assert panel.sign_in("s", emails["superadmin"], PASSWORD).status == 200
    assert panel.get("s", "/me").status == 200


@pytest.mark.parametrize("actor", ACTORS)
def test_lists_and_gets_are_scoped_per_role(
    panel: Panel, world: World, emails: dict[str, str], actor: str
) -> None:
    assert panel.sign_in("c", emails[actor], PASSWORD).status == 200

    visible_datasets = DATASETS[actor]
    visible_experiments = EXPERIMENTS[actor]
    cases_of = {name: name for name in world.datasets}
    experiment_dataset = {
        "exp_a": "ds_a",
        "exp_b": "ds_b",
        "exp_c": "ds_c",
        "exp_legacy": "ds_legacy",
        "exp_b_on_a": "ds_a",
    }
    expected: dict[str, tuple[dict[str, uuid.UUID], set[str]]] = {
        "Dataset": (world.datasets, visible_datasets),
        "TestCase": (
            world.cases,
            {n for n in world.cases if cases_of[n] in visible_datasets},
        ),
        "Experiment": (world.experiments, visible_experiments),
        "ExperimentResult": (world.results, visible_experiments),
        "EvaluationResult": (world.evaluations, visible_experiments),
        "User": (
            {n: p.user_id for n, p in world.principals.items()},
            USERS[actor],
        ),
        "Organization": (world.org_ids, ORGANIZATIONS.get(actor, set())),
    }
    assert set(experiment_dataset) == set(world.experiments)
    for model, (all_ids, visible) in expected.items():
        assert _ids(panel, "c", model) == _names(all_ids, visible), model
        for name, row_id in all_ids.items():
            reply = panel.get("c", f"/retrieve/{model}/{row_id}")
            assert reply.status == (200 if name in visible else 404), (
                model,
                name,
            )


def test_malformed_ids_are_not_found(
    panel: Panel, emails: dict[str, str]
) -> None:
    panel.sign_in("c", emails["superadmin"], PASSWORD)
    for model in ("Dataset", "User", "Organization"):
        reply = panel.get("c", f"/retrieve/{model}/not-a-uuid")
        assert reply.status == 404, model
    assert panel.get("c", f"/retrieve/Nope/{uuid.uuid4()}").status == 404


def test_lists_are_small_and_leave_out_bulky_columns(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("c", emails["superadmin"], PASSWORD)
    bulky = {
        "TestCase": ("input", "expected_output"),
        "Experiment": ("prompt_template", "config", "evaluator_config"),
        "ExperimentResult": (
            "rendered_prompt",
            "response_text",
            "error_message",
        ),
        "EvaluationResult": ("details",),
    }
    details = {
        "TestCase": world.cases["ds_a"],
        "Experiment": world.experiments["exp_a"],
        "ExperimentResult": world.results["exp_a"],
        "EvaluationResult": world.evaluations["exp_a"],
    }
    for model, columns in bulky.items():
        rows = panel.get("c", f"/list/{model}").json()["results"]
        assert rows, model
        for row in rows:
            assert not set(columns) & set(row), model
            assert "id" in row
        detail = panel.get("c", f"/retrieve/{model}/{details[model]}").json()
        assert set(columns) <= set(detail), model

    config = panel.get("c", "/configuration").json()["models"]
    assert {m["name"]: m["list_per_page"] for m in config} == {
        name: 25 for name in (*READ_ONLY_MODELS, "Organization")
    }


def test_password_hash_is_in_no_response(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("c", emails["superadmin"], PASSWORD)
    responses = [
        panel.get("c", "/me"),
        panel.get("c", "/configuration"),
        panel.get("c", "/list/User", search="example"),
        panel.get("c", "/list/User", sort_by="email"),
    ]
    for principal in world.principals.values():
        responses.append(panel.get("c", f"/retrieve/User/{principal.user_id}"))
    for model in (*READ_ONLY_MODELS, "Organization"):
        responses.append(panel.get("c", f"/list/{model}"))
    for reply in responses:
        assert reply.status == 200
        assert "password_hash" not in reply.text
        assert "argon2" not in reply.text.lower()
    # The column cannot be reached as a filter, sort key or search either.
    for params in (
        {"password_hash__icontains": "argon"},
        {"sort_by": "password_hash"},
    ):
        assert panel.get("c", "/list/User", **params).status == 422


@pytest.mark.parametrize("actor", ["staff_a", "admin_a", "superadmin"])
async def test_models_are_read_only_on_the_server(
    panel: Panel,
    engine: AsyncEngine,
    world: World,
    emails: dict[str, str],
    actor: str,
) -> None:
    panel.sign_in("c", emails[actor], PASSWORD)
    own = world.principals[actor].user_id
    row_ids: dict[str, uuid.UUID] = {
        "Dataset": world.datasets["ds_a"],
        "TestCase": world.cases["ds_a"],
        "Experiment": world.experiments["exp_a"],
        "ExperimentResult": world.results["exp_a"],
        "EvaluationResult": world.evaluations["exp_a"],
        "User": own,
    }
    for model, row_id in row_ids.items():
        calls: list[tuple[str, str, Any]] = [
            ("POST", f"{API}/add/{model}", {"name": "x"}),
            ("PATCH", f"{API}/change/{model}/{row_id}", {"name": "x"}),
            ("DELETE", f"{API}/delete/{model}/{row_id}", None),
            ("POST", f"{API}/export/{model}", {"format": "CSV"}),
            ("POST", f"{API}/export/{model}", {"format": "JSON"}),
            ("POST", f"{API}/action/{model}/delete_selected", {"ids": []}),
        ]
        for method, path, body in calls:
            reply = panel.request("c", method, path, json_body=body)
            assert reply.status == 403, (model, method, path)

    # Nothing changed, and the user can still sign in with the same password.
    async with engine.connect() as conn:
        users = await conn.scalar(select(func.count()).select_from(User))
    assert users == len(world.principals)
    assert panel.sign_in("again", emails[actor], PASSWORD).status == 200


async def test_passwords_cannot_be_changed_through_the_panel(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("c", emails["superadmin"], PASSWORD)
    body = {
        "password": "new password 123",
        "confirm_password": "new password 123",
    }
    for target in ("superadmin", "staff_a"):
        reply = panel.request(
            "c",
            "PATCH",
            f"{API}/change-password/{world.principals[target].user_id}",
            json_body=body,
        )
        assert reply.status in (403, 404), target
    assert panel.sign_in("d", emails["superadmin"], PASSWORD).status == 200
    assert (
        panel.sign_in("e", emails["superadmin"], "new password 123").status
        == 401
    )


def test_data_routes_need_a_session(panel: Panel, world: World) -> None:
    row = world.datasets["ds_a"]
    routes: list[tuple[str, str, Any]] = [
        ("GET", "/me", None),
        ("GET", "/list/Dataset", None),
        ("GET", f"/retrieve/Dataset/{row}", None),
        ("POST", "/add/Dataset", {"name": "x"}),
        ("PATCH", f"/change/Dataset/{row}", {"name": "x"}),
        ("PATCH", f"/change-password/{row}", {"password": "x"}),
        ("DELETE", f"/delete/Dataset/{row}", None),
        ("POST", "/export/Dataset", {"format": "CSV"}),
        ("POST", "/action/Dataset/x", {"ids": []}),
        ("POST", "/widget-action/Dataset/x", {"query": []}),
        ("POST", "/sign-out", None),
    ]
    for method, path, body in routes:
        reply = panel.request("anon", method, API + path, json_body=body)
        assert reply.status == 401, (method, path)
    config = panel.get("anon", "/configuration").json()
    assert config["models"] == []
