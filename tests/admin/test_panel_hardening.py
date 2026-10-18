"""Hardening of the panel found by the security review: session lifetime,
unbound-user safety, malformed filters, display strings and limits."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import ADMIN_SECRET, API, Panel

pytestmark = pytest.mark.integration

COOKIE = "admin_session_id"
SENTINELS = {
    "created_at__gte": "SENTINEL-DATE-5a1c",
    "id__exact": "SENTINEL-UUID-77d0",
    "position__lt": "SENTINEL-INT-e3b9",
    "name__icontains": "SENTINEL-TEXT-'\"%_\\-0c42",
}


def _cookie(user_id: object, *, expires: timedelta) -> str:
    return jwt.encode(
        {
            "user_id": str(user_id),
            "session_expired_at": (datetime.now(UTC) + expires).isoformat(),
        },
        ADMIN_SECRET,
        algorithm="HS256",
    )


def _as(cookie: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={cookie}"}


def test_session_older_than_its_ttl_is_rejected(
    panel: Panel, world: World
) -> None:
    user = world.principals["staff_a"].user_id
    fresh = _cookie(user, expires=timedelta(minutes=5))
    stale = _cookie(user, expires=-timedelta(seconds=1))
    ok = panel.request("c", "GET", f"{API}/me", headers=_as(fresh))
    assert ok.status == 200
    for path in ("/me", "/list/Dataset"):
        reply = panel.request("c", "GET", API + path, headers=_as(stale))
        assert reply.status == 401, path


def test_session_cookie_expiry_follows_the_ttl_and_is_samesite_lax(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel(extra_env={"ADMIN_SESSION_TTL_SECONDS": "120"})
    try:
        reply = panel.sign_in("c", emails["staff_a"], PASSWORD)
        assert reply.status == 200
        header = reply.set_cookie[0]
        assert "samesite=lax" in header.lower()
        assert "httponly" in header.lower()
        token = header.split(";", 1)[0].split("=", 1)[1]
        claims = jwt.decode(token, ADMIN_SECRET, algorithms=["HS256"])
        expires = datetime.fromisoformat(claims["session_expired_at"])
        left = (expires.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
        assert 60 < left <= 120
    finally:
        panel.close()


def test_samesite_cannot_be_weakened_from_the_environment(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel(extra_env={"ADMIN_SESSION_COOKIE_SAMESITE": "none"})
    try:
        reply = panel.sign_in("c", emails["staff_a"], PASSWORD)
        assert "samesite=lax" in reply.set_cookie[0].lower()
    finally:
        panel.close()


def test_unbound_user_never_serialises_a_principal(
    world: World, emails: dict[str, str]
) -> None:
    """If fastadmin's second user lookup came back empty, the data routes of
    the user admin would run in its session-validating branch."""
    panel = Panel(extra_env={"PANEL_SIMULATE_UNBOUND_USER": "1"})
    try:
        assert panel.sign_in("c", emails["admin_a"], PASSWORD).status == 200
        staff = world.principals["staff_a"]
        leaked = {
            str(staff.user_id),
            str(staff.organization_id),
            str(world.principals["admin_a"].user_id),
            "superadmin",
            "admin",
            "staff",
        }
        paths = [
            "/me",
            f"/retrieve/User/{staff.user_id}",
            f"/retrieve/User/{world.principals['superadmin'].user_id}",
            f"/retrieve/Dataset/{world.datasets['ds_a']}",
        ]
        for path in paths:
            reply = panel.get("c", path)
            assert reply.status in (401, 403, 404), (path, reply.text)
            assert not any(item in reply.text for item in leaked), path
        # Without the user bound, lists fail closed as well.
        listing = panel.get("c", "/list/User")
        assert listing.status == 200 and listing.json()["results"] == []
    finally:
        panel.close()


def test_malformed_filters_are_controlled_and_not_logged(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel(debug_logs=True)
    try:
        assert panel.sign_in("c", emails["superadmin"], PASSWORD).status == 200
        replies = []
        for model, key in (
            ("Dataset", "created_at__gte"),
            ("Dataset", "id__exact"),
            ("TestCase", "position__lt"),
            ("Dataset", "name__icontains"),
        ):
            replies.append(
                panel.get("c", f"/list/{model}", **{key: SENTINELS[key]})
            )
        replies.append(
            panel.get("c", "/list/Dataset", search=SENTINELS["name__icontains"])
        )
        replies.append(
            panel.get("c", "/list/Dataset", sort_by=SENTINELS["id__exact"])
        )
    finally:
        logs = panel.close()
    # Typed columns reject garbage with a 422; text columns take any text.
    assert [r.status for r in replies[:3]] == [422, 422, 422]
    for reply in replies[3:5]:
        assert reply.status == 200
        assert reply.json()["results"] == []
    assert replies[5].status == 422  # not a sortable field
    for value in SENTINELS.values():
        assert value not in logs


def test_well_formed_filters_still_work(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel()
    try:
        assert panel.sign_in("c", emails["superadmin"], PASSWORD).status == 200
        ds = world.datasets["ds_a"]
        by_id = panel.get("c", "/list/Dataset", id__exact=str(ds))
        assert [r["id"] for r in by_id.json()["results"]] == [str(ds)]
        by_date = panel.get(
            "c", "/list/Dataset", created_at__gte="2000-01-01T00:00:00"
        )
        assert by_date.json()["total"] == 4
        by_pos = panel.get("c", "/list/TestCase", position__lt="1")
        assert by_pos.json()["total"] == 4
        none = panel.get("c", "/list/TestCase", position__lt="0")
        assert none.json()["total"] == 0
    finally:
        panel.close()


def test_rows_have_a_safe_display_string(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel()
    try:
        assert panel.sign_in("c", emails["superadmin"], PASSWORD).status == 200
        texts = []
        for model in (
            "Dataset",
            "TestCase",
            "Experiment",
            "ExperimentResult",
            "EvaluationResult",
            "User",
            "Organization",
        ):
            listing = panel.get("c", f"/list/{model}")
            rows = listing.json()["results"]
            assert rows, model
            texts.append(listing.text)
            for row in rows:
                assert row["__str__"].startswith(model)
                detail = panel.get("c", f"/retrieve/{model}/{row['id']}")
                assert detail.json()["__str__"] == row["__str__"]
                texts.append(detail.text)
    finally:
        panel.close()
    for text in texts:
        assert "components." not in text
        assert "object at 0x" not in text


def test_list_limit_is_clamped_to_the_maximum(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel(extra_env={"ADMIN_QUERY_MAX_LIMIT": "2"})
    try:
        assert panel.sign_in("c", emails["superadmin"], PASSWORD).status == 200
        reply = panel.get("c", "/list/Dataset", limit=100000)
        assert reply.status == 200
        assert len(reply.json()["results"]) == 2
        assert reply.json()["total"] == 4
        negative = panel.get("c", "/list/Dataset", limit=-5, offset=-1)
        assert len(negative.json()["results"]) == 2
    finally:
        panel.close()


def test_filters_cannot_reach_another_organizations_rows(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel()
    try:
        assert panel.sign_in("a", emails["admin_a"], PASSWORD).status == 200
        other = str(world.org_ids["b"])
        foreign = {
            "Dataset": world.datasets["ds_c"],
            "Experiment": world.experiments["exp_c"],
            "User": world.principals["staff_c"].user_id,
        }
        for model, row in foreign.items():
            queries: list[dict[str, Any]] = [
                {"id__exact": str(row)},
                {"id__in": str(row)},
                {"organization_id__exact": other},
                {"organization_id__in": other},
                {"organization_id__icontains": other[:8]},
                {"sort_by": "organization_id", "id__exact": str(row)},
            ]
            for query in queries:
                reply = panel.get("a", f"/list/{model}", **query)
                assert reply.status in (200, 422), (model, query, reply.text)
                if reply.status == 200:
                    assert reply.json()["results"] == [], (model, query)
                    assert reply.json()["total"] == 0, (model, query)
        search = panel.get("a", "/list/Dataset", search="ds_c")
        assert search.json()["results"] == []
        own = panel.get(
            "a", "/list/Dataset", organization_id__exact=str(world.org_ids["a"])
        )
        assert own.status in (200, 422), own.text
        if own.status == 200:
            assert own.json()["total"] == 3
    finally:
        panel.close()


async def test_engine_hides_statement_parameters() -> None:
    from components.core.database import create_engine

    engine = create_engine("postgresql+asyncpg://u:p@localhost/db")
    try:
        assert engine.sync_engine.hide_parameters is True
    finally:
        await engine.dispose()
