"""Seeding helper: stores experiments with exact, hand-picked results."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.schemas import Principal
from components.datasets.models import Dataset, TestCase
from components.evaluation.models import EvaluationResult
from components.experiments.models import Experiment, ExperimentResult
from tests.access import seed_principal

Factory = async_sessionmaker[AsyncSession]


@dataclass
class Row:
    position: int
    model: str = "m1"
    provider: str = "openai"
    status: str = "succeeded"
    latency: float | None = None
    tokens: tuple[int | None, int | None, int | None] = (None, None, None)
    error_kind: str | None = None
    evals: dict[str, str] = field(default_factory=dict)
    text: str | None = None
    attempts: int = 1


def targets(*models: str) -> list[dict[str, str]]:
    return [{"provider": "openai", "model": m} for m in models]


def evaluator_config(*ids: str) -> dict[str, Any]:
    return {"evaluators": [{"id": i, "type": "exact_match"} for i in ids]}


async def seed(
    factory: Factory,
    rows: list[Row],
    models: tuple[str, ...] = ("m1",),
    evaluators: tuple[str, ...] = (),
    owner: Principal | None = None,
) -> uuid.UUID:
    """Store an experiment owned by ``owner`` (a new staff user by
    default)."""
    owner = owner or await seed_principal(factory)
    async with factory() as session:
        dataset = Dataset(
            name="d",
            organization_id=owner.organization_id,
            owner_id=owner.user_id,
        )
        session.add(dataset)
        await session.flush()
        cases = {
            p: TestCase(
                dataset_id=dataset.id,
                position=p,
                input=f"in{p}",
                expected_output=f"out{p}",
            )
            for p in sorted({r.position for r in rows})
        }
        session.add_all(cases.values())
        experiment = Experiment(
            name="e",
            dataset_id=dataset.id,
            organization_id=dataset.organization_id,
            owner_id=owner.user_id,
            status="completed",
            prompt_template="{input}",
            config={"targets": targets(*models)},
            evaluator_config=evaluator_config(*evaluators),
        )
        session.add(experiment)
        await session.flush()
        for row in rows:
            result = ExperimentResult(
                experiment_id=experiment.id,
                test_case_id=cases[row.position].id,
                case_position=row.position,
                provider=row.provider,
                model=row.model,
                status=row.status,
                rendered_prompt=f"SECRET-PROMPT-{row.position}",
                attempts=row.attempts,
                response_text=row.text,
                input_tokens=row.tokens[0],
                output_tokens=row.tokens[1],
                total_tokens=row.tokens[2],
                latency_ms=row.latency,
                error_kind=row.error_kind,
            )
            session.add(result)
            await session.flush()
            for evaluator_id, status in row.evals.items():
                session.add(
                    EvaluationResult(
                        experiment_result_id=result.id,
                        experiment_id=experiment.id,
                        evaluator_id=evaluator_id,
                        evaluator_type="exact_match",
                        evaluator_version=1,
                        status=status,
                        details={},
                    )
                )
        await session.commit()
        return experiment.id
