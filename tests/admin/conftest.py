import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import cast

import pytest
from sqlalchemy import Table, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from components.auth.security import hash_password
from components.core.database import Base, create_engine
from components.core.enums import Role
from components.core.schemas import Principal
from components.datasets.models import Dataset, TestCase
from components.evaluation.models import EvaluationResult
from components.experiments.models import Experiment, ExperimentResult
from components.users.models import User
from tests.access import (
    ACCESS_MODELS,
    PASSWORD,
    seed_organization,
    seed_principal,
)
from tests.admin.panel_process import Panel

_MODELS = (
    *ACCESS_MODELS,
    Dataset,
    TestCase,
    Experiment,
    ExperimentResult,
    EvaluationResult,
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Engine on ``DATABASE_URL`` with fresh tables per test."""
    engine = create_engine(os.environ["DATABASE_URL"])
    tables = [cast(Table, m.__table__) for m in _MODELS]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
    await engine.dispose()


@dataclass
class World:
    """Two organizations with ids by name for every seeded row.

    Org A: staff_a, staff_b, admin_a. Org B: staff_c, admin_b. Plus a
    superadmin. Each dataset has one case; each experiment one result and
    one evaluation. ``legacy`` rows have no owner and ``exp_b_on_a`` is an
    experiment of staff_b on staff_a's dataset.
    """

    principals: dict[str, Principal]
    org_ids: dict[str, uuid.UUID]
    datasets: dict[str, uuid.UUID]
    cases: dict[str, uuid.UUID]
    experiments: dict[str, uuid.UUID]
    results: dict[str, uuid.UUID]
    evaluations: dict[str, uuid.UUID]


@pytest.fixture
async def world(engine: AsyncEngine) -> World:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        org_a = await seed_organization(session)
        org_b = await seed_organization(session)
        await session.commit()
    principals = {
        "staff_a": await seed_principal(factory, Role.STAFF, org_a),
        "staff_b": await seed_principal(factory, Role.STAFF, org_a),
        "admin_a": await seed_principal(factory, Role.ADMIN, org_a),
        "staff_c": await seed_principal(factory, Role.STAFF, org_b),
        "admin_b": await seed_principal(factory, Role.ADMIN, org_b),
        "superadmin": await seed_principal(factory, Role.SUPERADMIN),
    }
    # name -> (organization, owner)
    dataset_spec = {
        "ds_a": (org_a, "staff_a"),
        "ds_b": (org_a, "staff_b"),
        "ds_c": (org_b, "staff_c"),
        "ds_legacy": (org_a, None),
    }
    # name -> (dataset, owner)
    experiment_spec = {
        "exp_a": ("ds_a", "staff_a"),
        "exp_b": ("ds_b", "staff_b"),
        "exp_c": ("ds_c", "staff_c"),
        "exp_legacy": ("ds_legacy", None),
        "exp_b_on_a": ("ds_a", "staff_b"),
    }
    world = World(principals, {"a": org_a, "b": org_b}, {}, {}, {}, {}, {})
    async with factory() as session:
        await _seed_data(session, world, dataset_spec, experiment_spec)
        await session.commit()
    return world


async def _seed_data(
    session: AsyncSession,
    world: World,
    dataset_spec: dict[str, tuple[uuid.UUID, str | None]],
    experiment_spec: dict[str, tuple[str, str | None]],
) -> None:
    for name, (org, owner) in dataset_spec.items():
        dataset = Dataset(
            name=name,
            organization_id=org,
            owner_id=world.principals[owner].user_id if owner else None,
        )
        session.add(dataset)
        await session.flush()
        world.datasets[name] = dataset.id
        case = TestCase(dataset_id=dataset.id, position=0, input=f"in {name}")
        session.add(case)
        await session.flush()
        world.cases[name] = case.id
    for name, (dataset_name, owner) in experiment_spec.items():
        organization = next(
            org for n, (org, _) in dataset_spec.items() if n == dataset_name
        )
        experiment = Experiment(
            name=name,
            dataset_id=world.datasets[dataset_name],
            organization_id=organization,
            owner_id=world.principals[owner].user_id if owner else None,
            prompt_template="{{input}}",
            config={},
            evaluator_config={},
        )
        session.add(experiment)
        await session.flush()
        world.experiments[name] = experiment.id
        result = ExperimentResult(
            experiment_id=experiment.id,
            test_case_id=world.cases[dataset_name],
            case_position=0,
            provider="openai",
            model="m",
            rendered_prompt="p",
        )
        session.add(result)
        await session.flush()
        world.results[name] = result.id
        evaluation = EvaluationResult(
            experiment_result_id=result.id,
            experiment_id=experiment.id,
            evaluator_id="exact",
            evaluator_type="exact_match",
            evaluator_version=1,
            status="passed",
            details={},
        )
        session.add(evaluation)
        await session.flush()
        world.evaluations[name] = evaluation.id


@pytest.fixture
async def emails(engine: AsyncEngine, world: World) -> dict[str, str]:
    """Sign-in emails by principal name; every user gets ``PASSWORD``."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            update(User).values(password_hash=await hash_password(PASSWORD))
        )
        await session.commit()
        rows = (await session.execute(select(User.id, User.email))).all()
    by_id = {row.id: row.email for row in rows}
    return {
        name: by_id[principal.user_id]
        for name, principal in world.principals.items()
    }


@pytest.fixture
def panel(emails: dict[str, str]) -> Iterator[Panel]:
    """The panel mounted in a child process on the test database."""
    running = Panel()
    yield running
    running.close()
