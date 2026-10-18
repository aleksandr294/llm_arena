"""Secrets, prompts, responses and provider error text must reach neither
logs nor stored error messages (success and failure paths)."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.endpoints.experiments import get_enqueue, get_llm_service
from components.core.database import get_session
from components.core.schemas import Principal
from components.experiments.runner import ExperimentRunner
from components.third_party.llm.providers.base import LLMProviderError
from components.third_party.llm.schemas import LLMErrorKind, ProviderName
from tests.access import Actors
from tests.experiments.helpers import (
    Enqueued,
    Factory,
    FakeLLM,
    make_dataset,
    response,
)

pytestmark = pytest.mark.integration

URL = "/api/v1/experiments"
API_KEY = "sk-SENTINEL-KEY-71c2"
PROMPT = "SENTINEL-PROMPT-4e1d"
REPLY = "SENTINEL-REPLY-93aa"
ERROR_TEXT = "SENTINEL-PROVIDER-ERROR-5b07"
EXC_TEXT = "SENTINEL-EXCEPTION-22f8"
SENTINELS = (API_KEY, PROMPT, REPLY, ERROR_TEXT, EXC_TEXT)


@pytest.fixture(autouse=True)
def _overrides(
    app: FastAPI,
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = session_override


@pytest.fixture
async def client(actors: Actors) -> AsyncClient:
    """Authenticated as staff user A; shadows the anonymous root client."""
    return await actors.client("staff_a")


@pytest.fixture
def owner(actors: Actors) -> Principal:
    return actors.principal("staff_a")


async def test_no_sentinel_in_logs_or_stored_errors(
    app: FastAPI,
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
    owner: Principal,
) -> None:
    caplog.set_level(logging.DEBUG)
    enqueued = Enqueued()
    calls = 0

    async def handler(prompt: str) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(text=REPLY)
        if calls == 2:
            raise LLMProviderError(
                LLMErrorKind.AUTHENTICATION,
                ProviderName.OPENAI,
                f"{ERROR_TEXT} {API_KEY} {prompt}",
                request_id="req-x",
            )
        raise RuntimeError(f"{EXC_TEXT} {API_KEY} {prompt}")

    llm = FakeLLM()
    llm.handler = handler  # type: ignore[assignment]
    app.dependency_overrides[get_llm_service] = llm.as_service
    app.dependency_overrides[get_enqueue] = lambda: enqueued

    dataset_id = await make_dataset(session_factory, 3, owner)
    # Put the prompt sentinel into the case content.
    async with session_factory() as session:
        await session.execute(
            text("UPDATE test_cases SET input = :v"), {"v": PROMPT}
        )
        await session.commit()
    created = await client.post(
        URL,
        json={
            "name": "exp",
            "dataset_id": str(dataset_id),
            "prompt_template": "Q: {input}",
            "config": {"targets": [{"provider": "openai", "model": "m1"}]},
        },
    )
    experiment_id = created.json()["id"]
    await client.post(f"{URL}/{experiment_id}/start")
    runner = ExperimentRunner(
        session_factory, llm.as_service(), 4, jitter=lambda: 0.0
    )
    for result_id in enqueued.flat:
        await runner.run(result_id)
    # Error paths through the API.
    await client.post(f"{URL}/{uuid.uuid4()}/start")
    await client.post(f"{URL}/{experiment_id}/start")

    results = (await client.get(f"{URL}/{experiment_id}/results")).json()
    by_status = {r["status"] for r in results["items"]}
    assert "succeeded" in by_status
    assert "failed" in by_status
    for item in results["items"]:
        assert item["error_message"] is None or not any(
            s in item["error_message"] for s in SENTINELS
        )
    kinds = {r["error_kind"] for r in results["items"]}
    assert {"authentication", "internal"} <= kinds

    for sentinel in SENTINELS:
        assert sentinel not in caplog.text
    await client.post(f"{URL}/{experiment_id}/retry-failed")
    for sentinel in SENTINELS:
        assert sentinel not in caplog.text
    # The reply is stored (it is the result) but never logged.
    assert any(r["response_text"] == REPLY for r in results["items"])
