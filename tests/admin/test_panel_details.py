"""Computed read-only keys of the panel's detail views."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from components.auth.models import RefreshToken
from components.experiments.models import ExperimentResult
from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import API, Panel

pytestmark = pytest.mark.integration

COMPUTED = {
    "Dataset": {"owner_email", "test_case_count"},
    "TestCase": {"dataset_name"},
    "Experiment": {"dataset_name", "owner_email", "results_by_status"},
    "ExperimentResult": {"experiment_name"},
    "Organization": {"user_count"},
    "User": {"organization_name", "active_refresh_tokens"},
}
HASHES = ["a" * 64, "b" * 64, "c" * 64, "d" * 64]


def _detail(panel: Panel, client: str, model: str, row: uuid.UUID) -> Any:
    reply = panel.get(client, f"/retrieve/{model}/{row}")
    assert reply.status == 200, reply.text
    return reply.json()


async def _seed_tokens_and_results(engine: AsyncEngine, world: World) -> None:
    user = world.principals["staff_a"].user_id
    now = datetime.now(UTC)
    day = timedelta(days=1)
    async with engine.begin() as conn:
        for values in (
            dict(expires_at=now + day, token_hash=HASHES[0]),
            dict(expires_at=now + day, token_hash=HASHES[1], revoked_at=now),
            dict(expires_at=now - day, token_hash=HASHES[2]),
            dict(expires_at=now + day, token_hash=HASHES[3], used_at=now),
        ):
            await conn.execute(
                insert(RefreshToken).values(
                    user_id=user, family_id=uuid.uuid4(), **values
                )
            )
        for status, model in (("completed", "m2"), ("failed", "m3")):
            await conn.execute(
                insert(ExperimentResult).values(
                    experiment_id=world.experiments["exp_a"],
                    test_case_id=world.cases["ds_a"],
                    case_position=0,
                    provider="openai",
                    model=model,
                    status=status,
                    rendered_prompt="p",
                )
            )


async def test_details_have_computed_keys_and_lists_do_not(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    await _seed_tokens_and_results(engine, world)
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    rows = {
        "Dataset": world.datasets["ds_a"],
        "TestCase": world.cases["ds_a"],
        "Experiment": world.experiments["exp_a"],
        "ExperimentResult": world.results["exp_a"],
        "Organization": world.org_ids["a"],
        "User": world.principals["staff_a"].user_id,
    }
    for model, row in rows.items():
        detail = _detail(panel, "root", model, row)
        assert COMPUTED[model] <= set(detail), model
        listing = panel.get("root", f"/list/{model}", limit=100).json()
        for item in listing["results"]:
            assert not COMPUTED[model] & set(item), model

    dataset = _detail(panel, "root", "Dataset", rows["Dataset"])
    assert dataset["test_case_count"] == 1
    assert dataset["owner_email"] == emails["staff_a"]
    assert _detail(panel, "root", "TestCase", rows["TestCase"])[
        "dataset_name"
    ] == ("ds_a")
    experiment = _detail(panel, "root", "Experiment", rows["Experiment"])
    assert experiment["dataset_name"] == "ds_a"
    assert experiment["owner_email"] == emails["staff_a"]
    assert experiment["results_by_status"] == {
        "pending": 1,
        "completed": 1,
        "failed": 1,
    }
    result = _detail(
        panel, "root", "ExperimentResult", rows["ExperimentResult"]
    )
    assert result["experiment_name"] == "exp_a"
    assert result["case_position"] == 0
    assert _detail(panel, "root", "Organization", rows["Organization"])[
        "user_count"
    ] == (3)
    user = _detail(panel, "root", "User", rows["User"])
    assert user["active_refresh_tokens"] == 1
    assert user["organization_name"]
    assert (
        _detail(panel, "root", "User", world.principals["superadmin"].user_id)[
            "organization_name"
        ]
        is None
    )


async def test_no_token_or_hash_value_in_any_response(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    await _seed_tokens_and_results(engine, world)
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    texts = [
        panel.get("root", "/configuration").text,
        panel.get("root", "/me").text,
    ]
    for principal in world.principals.values():
        texts.append(
            panel.get("root", f"/retrieve/User/{principal.user_id}").text
        )
    texts.append(panel.get("root", "/list/User", limit=100).text)
    for text in texts:
        for value in HASHES:
            assert value not in text
        for word in ("token_hash", "password_hash", "argon2", "family_id"):
            assert word not in text.lower()


async def test_details_respect_the_scope(
    panel: Panel, engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    await _seed_tokens_and_results(engine, world)
    # staff_b sees its experiment on staff_a's dataset, but not the dataset
    # or its owner: the parent yields no value.
    panel.sign_in("b", emails["staff_b"], PASSWORD)
    shared = _detail(panel, "b", "Experiment", world.experiments["exp_b_on_a"])
    assert shared["dataset_name"] is None
    assert shared["owner_email"] == emails["staff_b"]
    assert shared["results_by_status"] == {"pending": 1}
    own = _detail(panel, "b", "Experiment", world.experiments["exp_b"])
    assert own["dataset_name"] == "ds_b"
    # Results of another owner's experiment are not counted.
    assert (
        panel.get(
            "b", f"/retrieve/Experiment/{world.experiments['exp_a']}"
        ).status
        == 404
    )

    # Admins do not see organizations, so the name is absent; the counts of
    # the visible user stay.
    panel.sign_in("adm", emails["admin_a"], PASSWORD)
    user = _detail(panel, "adm", "User", world.principals["staff_a"].user_id)
    assert user["organization_name"] is None
    assert user["active_refresh_tokens"] == 1
    legacy = _detail(panel, "adm", "Dataset", world.datasets["ds_legacy"])
    assert legacy["owner_email"] is None
    assert legacy["test_case_count"] == 1
    # Another organization's user does not exist for this admin.
    other = world.principals["staff_c"].user_id
    assert panel.get("adm", f"/retrieve/User/{other}").status == 404

    # Staff see only themselves, with their own counts.
    panel.sign_in("a", emails["staff_a"], PASSWORD)
    me = _detail(panel, "a", "User", world.principals["staff_a"].user_id)
    assert me["active_refresh_tokens"] == 1


async def test_computed_keys_are_read_only_in_the_interface(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    panel.sign_in("root", emails["superadmin"], PASSWORD)
    models = {
        m["name"]: m
        for m in panel.get("root", "/configuration").json()["models"]
    }
    for model, keys in COMPUTED.items():
        fields = {f["name"]: f for f in models[model]["fields"]}
        for key in keys:
            field = fields[key]
            assert field["list_configuration"] is None, key
            props = field["change_configuration"]["form_widget_props"]
            assert props["readOnly"] is True and props["disabled"] is True
        # Lists show the same columns as before.
        columns = [
            name
            for name, f in fields.items()
            if f["list_configuration"] is not None
        ]
        assert not set(columns) & keys

    # A change request cannot write a computed key (nor anything else).
    before = _detail(panel, "root", "Organization", world.org_ids["a"])
    reply = panel.request(
        "root",
        "PATCH",
        f"{API}/change/Organization/{world.org_ids['a']}",
        json_body={"user_count": 99, "is_active": True},
    )
    assert reply.status == 200
    after = _detail(panel, "root", "Organization", world.org_ids["a"])
    assert after["user_count"] == before["user_count"] == 3
    # Nothing can be added or deleted through the new keys.
    for method, path in (
        ("POST", f"{API}/add/Dataset"),
        ("DELETE", f"{API}/delete/Dataset/{world.datasets['ds_a']}"),
    ):
        assert panel.request("root", method, path, json_body={}).status == 403
