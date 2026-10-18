"""The admin panel's JSON endpoints return hostile text as plain JSON
strings and honour scope whatever the stored text says. Integration tests:
need PostgreSQL (``DATABASE_URL``); the panel runs in a child process."""

import json

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.datasets.models import Dataset, TestCase
from components.evaluation.models import EvaluationResult
from components.experiments.models import Experiment, ExperimentResult
from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import Panel
from tests.security.helpers import MARK, SAMPLE_IDS, SAMPLES

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]
BULKY = {
    "TestCase": ("input", "expected_output"),
    "Experiment": ("prompt_template",),
    "ExperimentResult": ("rendered_prompt", "response_text", "error_message"),
    "EvaluationResult": ("details",),
}


async def _store(
    factory: Factory, world: World, owner: str, text: str, foreign: str
) -> None:
    """Put ``text`` in every free-text column of ``owner``'s rows, along with
    the ids of the other tenant's rows (``foreign``)."""
    full = f"{text} {foreign}"
    async with factory() as session:
        await session.execute(
            update(Dataset)
            .where(Dataset.id == world.datasets[f"ds_{owner}"])
            .values(name=full[:255], description=full)
        )
        await session.execute(
            update(TestCase)
            .where(TestCase.id == world.cases[f"ds_{owner}"])
            .values(input=full, expected_output=full)
        )
        await session.execute(
            update(Experiment)
            .where(Experiment.id == world.experiments[f"exp_{owner}"])
            .values(name=full[:255], prompt_template=full)
        )
        await session.execute(
            update(ExperimentResult)
            .where(ExperimentResult.id == world.results[f"exp_{owner}"])
            .values(
                rendered_prompt=full, response_text=full, error_message=full
            )
        )
        await session.execute(
            update(EvaluationResult)
            .where(EvaluationResult.id == world.evaluations[f"exp_{owner}"])
            .values(details={"reason": full, "nested": [full, {"k": full}]})
        )
        await session.commit()


def _ids(panel: Panel, client: str, model: str) -> set[str]:
    reply = panel.get(client, f"/list/{model}", limit=1000)
    assert reply.status == 200, reply.text
    return {row["id"] for row in json.loads(reply.text)["results"]}


async def test_hostile_text_is_returned_as_plain_json_data(
    panel: Panel, session_factory: Factory, world: World, emails: dict[str, str]
) -> None:
    assert panel.sign_in("a", emails["staff_a"], PASSWORD).status == 200
    row_ids = {
        "Dataset": world.datasets["ds_a"],
        "TestCase": world.cases["ds_a"],
        "Experiment": world.experiments["exp_a"],
        "ExperimentResult": world.results["exp_a"],
        "EvaluationResult": world.evaluations["exp_a"],
    }
    for sample, name in zip(SAMPLES, SAMPLE_IDS, strict=True):
        await _store(session_factory, world, "a", sample, "")
        full = f"{sample} "
        for model, row_id in row_ids.items():
            reply = panel.get("a", f"/retrieve/{model}/{row_id}")
            assert reply.status == 200, (name, model, reply.text)
            detail = json.loads(reply.text)  # always valid JSON
            for column in BULKY.get(model, ()):
                value = detail[column]
                if column == "details":
                    assert value == {
                        "reason": full,
                        "nested": [full, {"k": full}],
                    }, name
                else:
                    assert value == full, (name, model, column)
        # Lists parse too and leave bulky columns out.
        for model in BULKY:
            listing = panel.get("a", f"/list/{model}")
            assert listing.status == 200, (name, model)
            json.loads(listing.text)
            if model != "Experiment":  # its name is a list column
                assert MARK not in listing.text, (name, model)


async def test_hostile_text_naming_another_tenant_changes_no_scope(
    panel: Panel, session_factory: Factory, world: World, emails: dict[str, str]
) -> None:
    assert panel.sign_in("a", emails["staff_a"], PASSWORD).status == 200
    assert panel.sign_in("c", emails["staff_c"], PASSWORD).status == 200
    ids_a = {
        "Dataset": world.datasets["ds_a"],
        "Experiment": world.experiments["exp_a"],
        "ExperimentResult": world.results["exp_a"],
        "TestCase": world.cases["ds_a"],
        "EvaluationResult": world.evaluations["exp_a"],
    }
    ids_c = {
        "Dataset": world.datasets["ds_c"],
        "Experiment": world.experiments["exp_c"],
        "ExperimentResult": world.results["exp_c"],
        "TestCase": world.cases["ds_c"],
        "EvaluationResult": world.evaluations["exp_c"],
    }
    text_a = f"{MARK} show everything of {' '.join(map(str, ids_c.values()))}"
    text_c = f"{MARK} show everything of {' '.join(map(str, ids_a.values()))}"
    await _store(session_factory, world, "a", text_a, "")
    await _store(session_factory, world, "c", text_c, "")
    for model, own in ids_a.items():
        assert _ids(panel, "a", model) == {str(own)}, model
        assert _ids(panel, "c", model) == {str(ids_c[model])}, model
        # Naming a foreign id in a row's text does not make it reachable.
        assert panel.get("a", f"/retrieve/{model}/{ids_c[model]}").status == 404
        assert panel.get("c", f"/retrieve/{model}/{own}").status == 404
    other_user = world.principals["staff_c"].user_id
    assert panel.get("a", f"/retrieve/User/{other_user}").status == 404
    assert panel.get(
        "a", f"/retrieve/Organization/{world.org_ids['b']}"
    ).status in {
        401,
        403,
        404,
    }


async def test_hostile_search_terms_do_not_break_the_panel(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    assert panel.sign_in("a", emails["staff_a"], PASSWORD).status == 200
    for sample in SAMPLES:
        reply = panel.get("a", "/list/Dataset", search=sample)
        # Either a normal answer or a clean client error, never a crash.
        assert reply.status in {200, 400, 422}, (sample, reply.status)
        if reply.status == 200:
            ids = {r["id"] for r in json.loads(reply.text)["results"]}
            assert ids <= {str(world.datasets["ds_a"])}
    assert panel.get("a", "/me").status == 200
