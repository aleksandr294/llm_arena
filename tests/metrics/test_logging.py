"""Prompts, responses and expected outputs must not reach DEBUG logs during
metrics and export requests: need PostgreSQL (``DATABASE_URL``)."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.endpoints.metrics import get_factory, get_pricing_file
from components.core.database import get_session, session_scope
from components.core.schemas import Principal
from components.datasets.models import TestCase
from components.experiments.models import ExperimentResult
from tests.access import Actors
from tests.metrics.helpers import Row, seed

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]
RESPONSE = "SENTINEL-RESPONSE-7d3f"
EXPECTED = "SENTINEL-EXPECTED-19ac"
PROMPT = "SENTINEL-PROMPT-4e1d"
HOSTILE = f'=HYPERLINK("x")\n",\x01 {RESPONSE} {{"a": [ {EXPECTED}'


@pytest.fixture(autouse=True)
def _overrides(app: FastAPI, session_factory: Factory) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_scope(session_factory) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_factory] = lambda: session_factory
    app.dependency_overrides[get_pricing_file] = lambda: None


@pytest.fixture
async def client(actors: Actors) -> AsyncClient:
    """Authenticated as staff user A; shadows the anonymous root client."""
    return await actors.client("staff_a")


@pytest.fixture
def owner(actors: Actors) -> Principal:
    return actors.principal("staff_a")


async def _seed(factory: Factory, owner: Principal) -> uuid.UUID:
    experiment_id = await seed(
        factory,
        [
            Row(1, text=RESPONSE, evals={"ex": "passed"}),
            Row(2, text=HOSTILE, evals={"ex": "failed"}),
            Row(3, status="failed", error_kind="timeout"),
        ],
        evaluators=("ex",),
        owner=owner,
    )
    async with factory() as session:
        from sqlalchemy import select, update

        await session.execute(
            update(ExperimentResult)
            .where(ExperimentResult.experiment_id == experiment_id)
            .values(rendered_prompt=PROMPT)
        )
        await session.execute(update(TestCase).values(expected_output=EXPECTED))
        await session.commit()
        stored = (await session.scalars(select(ExperimentResult))).all()
        assert any(RESPONSE in (r.response_text or "") for r in stored)
    return experiment_id


async def test_sentinels_never_logged(
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
    owner: Principal,
) -> None:
    experiment_id = await _seed(session_factory, owner)
    caplog.set_level(logging.DEBUG)
    base = f"/api/v1/experiments/{experiment_id}"
    responses = [
        await client.get(f"{base}/summary"),
        await client.get(f"{base}/comparison"),
        await client.get(f"{base}/cases?include_responses=true"),
        await client.get(f"{base}/export"),
        await client.get(f"{base}/export?format=csv&include_responses=true"),
    ]
    assert [r.status_code for r in responses] == [200] * 5
    # The exports do carry the text (the check is meaningful), the
    # aggregate endpoints do not.
    assert RESPONSE in responses[3].text
    assert RESPONSE in responses[4].text
    assert RESPONSE not in responses[0].text + responses[1].text
    captured = caplog.text + "".join(r.getMessage() for r in caplog.records)
    assert "export done" in captured  # log capture is working
    for sentinel in (RESPONSE, EXPECTED, PROMPT, HOSTILE, "HYPERLINK"):
        assert sentinel not in captured
