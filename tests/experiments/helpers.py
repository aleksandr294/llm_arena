"""Shared fakes for experiment service and runner tests."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.schemas import Principal
from components.datasets.models import Dataset, TestCase
from components.experiments.schemas import (
    ExperimentConfigSchema,
    ExperimentCreateSchema,
)
from components.experiments.service import ExperimentService
from components.third_party.llm.providers.base import LLMProviderError
from components.third_party.llm.schemas import (
    LLMErrorKind,
    LLMResponseSchema,
    LLMUsageSchema,
    ModelTargetSchema,
    ProviderName,
)
from components.third_party.llm.service import (
    LLMService,
    ProviderNotConfiguredError,
)
from tests.access import ROOT, seed_principal

Factory = async_sessionmaker[AsyncSession]


def response(
    text: str = "ok", usage: LLMUsageSchema | None = None
) -> LLMResponseSchema:
    return LLMResponseSchema(
        text=text,
        provider=ProviderName.OPENAI,
        requested_model="m1",
        reported_model="m1-2024",
        finish_reason="stop",
        provider_request_id="req-1",
        usage=usage
        or LLMUsageSchema(input_tokens=3, output_tokens=4, total_tokens=7),
        effective_max_tokens=256,
        latency_ms=12.5,
    )


def provider_error(
    kind: LLMErrorKind, retry_after: float | None = None
) -> LLMProviderError:
    return LLMProviderError(
        kind,
        ProviderName.OPENAI,
        "secret prompt text",
        retry_after_seconds=retry_after,
        request_id="req-err",
    )


class FakeLLM:
    """Stands in for ``LLMService``. ``script`` items are consumed per call:
    a response is returned, an exception is raised. When exhausted, a plain
    success is returned. ``handler`` overrides everything."""

    def __init__(
        self,
        script: Sequence[LLMResponseSchema | Exception] = (),
        *,
        unconfigured: set[ProviderName] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.script = list(script)
        self.unconfigured = unconfigured or set()
        self.delay = delay
        self.calls: list[tuple[ModelTargetSchema, str, dict[str, Any]]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.handler: Callable[[str], Awaitable[LLMResponseSchema]] | None = (
            None
        )

    def ensure_provider_ready(self, name: ProviderName) -> None:
        if name in self.unconfigured:
            raise ProviderNotConfiguredError(name, "OPENAI_API_KEY")

    async def generate(
        self, target: ModelTargetSchema, prompt: str, **kwargs: Any
    ) -> LLMResponseSchema:
        self.calls.append((target, prompt, kwargs))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.handler is not None:
                return await self.handler(prompt)
            if self.script:
                item = self.script.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return response()
        finally:
            self.in_flight -= 1

    def as_service(self) -> LLMService:
        return cast(LLMService, self)


class Enqueued:
    """Fake enqueue port recording the ids it received."""

    def __init__(self) -> None:
        self.batches: list[list[uuid.UUID]] = []

    async def __call__(self, ids: Sequence[uuid.UUID]) -> None:
        self.batches.append(list(ids))

    @property
    def flat(self) -> list[uuid.UUID]:
        return [i for batch in self.batches for i in batch]


async def make_dataset(
    factory: Factory, cases: int = 3, owner: Principal | None = None
) -> uuid.UUID:
    """A dataset owned by ``owner`` (a new staff user by default)."""
    owner = owner or await seed_principal(factory)
    async with factory() as session:
        dataset = Dataset(
            name="d",
            organization_id=owner.organization_id,
            owner_id=owner.user_id,
        )
        session.add(dataset)
        await session.flush()
        session.add_all(
            [
                TestCase(
                    dataset_id=dataset.id,
                    position=p,
                    input=f"in{p}",
                    expected_output=f"out{p}",
                )
                for p in range(1, cases + 1)
            ]
        )
        await session.commit()
        return dataset.id


def create_data(
    dataset_id: uuid.UUID,
    models: Sequence[str] = ("m1",),
    max_attempts: int = 3,
) -> ExperimentCreateSchema:
    return ExperimentCreateSchema(
        name="exp",
        dataset_id=dataset_id,
        prompt_template="Q: {input}",
        config=ExperimentConfigSchema(
            targets=[
                ModelTargetSchema(provider=ProviderName.OPENAI, model=m)
                for m in models
            ],
            max_attempts=max_attempts,
        ),
    )


async def make_experiment(
    factory: Factory,
    cases: int = 3,
    models: Sequence[str] = ("m1",),
    max_attempts: int = 3,
    start: bool = False,
    enqueued: Enqueued | None = None,
    owner: Principal | None = None,
) -> uuid.UUID:
    owner = owner or await seed_principal(factory)
    dataset_id = await make_dataset(factory, cases, owner)
    enqueue = enqueued or Enqueued()
    async with factory() as session:
        service = ExperimentService(session, FakeLLM().as_service(), enqueue)
        experiment = await service.create(
            owner, create_data(dataset_id, models, max_attempts)
        )
        await session.commit()
        experiment_id = experiment.id
    if start:
        async with factory() as session:
            service = ExperimentService(
                session, FakeLLM().as_service(), enqueue
            )
            await service.start(ROOT, experiment_id)
    return experiment_id
