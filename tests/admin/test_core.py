"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import aliased, selectinload

from admin.core import ReadOnlyScopedAdmin, scoped_sessionmaker
from admin.schemas import SessionUser
from components.core.enums import Role
from components.core.schemas import Principal
from components.datasets.models import Dataset, TestCase
from components.evaluation.models import EvaluationResult
from components.experiments.models import Experiment, ExperimentResult
from components.organizations.models import Organization
from components.users.models import User
from tests.admin.helpers import World

pytestmark = pytest.mark.integration

ALL_DATASETS = {"ds_a", "ds_b", "ds_c", "ds_legacy"}
ALL_EXPERIMENTS = {"exp_a", "exp_b", "exp_c", "exp_legacy", "exp_b_on_a"}

# role actor -> visible dataset names / experiment names
DATASETS = {
    "staff_a": {"ds_a"},
    "staff_b": {"ds_b"},
    "staff_c": {"ds_c"},
    "admin_a": {"ds_a", "ds_b", "ds_legacy"},
    "admin_b": {"ds_c"},
    "superadmin": ALL_DATASETS,
}
EXPERIMENTS = {
    "staff_a": {"exp_a"},
    "staff_b": {"exp_b", "exp_b_on_a"},
    "staff_c": {"exp_c"},
    "admin_a": {"exp_a", "exp_b", "exp_b_on_a", "exp_legacy"},
    "admin_b": {"exp_c"},
    "superadmin": ALL_EXPERIMENTS,
}
USERS = {
    "staff_a": {"staff_a"},
    "staff_b": {"staff_b"},
    "staff_c": {"staff_c"},
    "admin_a": {"staff_a", "staff_b", "admin_a"},
    "admin_b": {"staff_c", "admin_b"},
    "superadmin": {
        "staff_a",
        "staff_b",
        "staff_c",
        "admin_a",
        "admin_b",
        "superadmin",
    },
}
ORGANIZATIONS = {
    "staff_a": set(),
    "admin_a": set(),
    "admin_b": set(),
    "superadmin": {"a", "b"},
}


def _dataset_of(experiment: str) -> str:
    return {
        "exp_a": "ds_a",
        "exp_b": "ds_b",
        "exp_c": "ds_c",
        "exp_legacy": "ds_legacy",
        "exp_b_on_a": "ds_a",
    }[experiment]


async def _ids(
    engine: AsyncEngine, principal: Principal | None, model: Any
) -> set[uuid.UUID]:
    async with scoped_sessionmaker(engine, principal)() as session:
        return set(await session.scalars(select(model.id)))


def _names(by_name: dict[str, uuid.UUID], names: set[str]) -> set[uuid.UUID]:
    return {by_name[n] for n in names}


@pytest.mark.parametrize("actor", sorted(DATASETS))
async def test_datasets_and_cases_follow_scope(
    engine: AsyncEngine, world: World, actor: str
) -> None:
    principal = world.principals[actor]
    assert await _ids(engine, principal, Dataset) == _names(
        world.datasets, DATASETS[actor]
    )
    # A case is visible exactly when its dataset is.
    assert await _ids(engine, principal, TestCase) == _names(
        world.cases, DATASETS[actor]
    )


@pytest.mark.parametrize("actor", sorted(EXPERIMENTS))
async def test_experiments_and_children_follow_scope(
    engine: AsyncEngine, world: World, actor: str
) -> None:
    principal = world.principals[actor]
    names = EXPERIMENTS[actor]
    assert await _ids(engine, principal, Experiment) == _names(
        world.experiments, names
    )
    assert await _ids(engine, principal, ExperimentResult) == _names(
        world.results, names
    )
    assert await _ids(engine, principal, EvaluationResult) == _names(
        world.evaluations, names
    )


@pytest.mark.parametrize("actor", sorted(USERS))
async def test_users_follow_scope(
    engine: AsyncEngine, world: World, actor: str
) -> None:
    visible = await _ids(engine, world.principals[actor], User)
    expected = {world.principals[n].user_id for n in USERS[actor]}
    assert visible == expected


async def test_admin_never_sees_superadmins(
    engine: AsyncEngine, world: World
) -> None:
    async with scoped_sessionmaker(
        engine, world.principals["admin_a"]
    )() as session:
        roles = set(await session.scalars(select(User.role)))
    assert Role.SUPERADMIN.value not in roles


@pytest.mark.parametrize("actor", sorted(ORGANIZATIONS))
async def test_organizations_only_for_superadmin(
    engine: AsyncEngine, world: World, actor: str
) -> None:
    visible = await _ids(engine, world.principals[actor], Organization)
    assert visible == _names(world.org_ids, ORGANIZATIONS[actor])


async def test_staff_cannot_see_legacy_rows(
    engine: AsyncEngine, world: World
) -> None:
    for actor in ("staff_a", "staff_b", "staff_c"):
        principal = world.principals[actor]
        assert world.datasets["ds_legacy"] not in await _ids(
            engine, principal, Dataset
        )
        assert world.experiments["exp_legacy"] not in await _ids(
            engine, principal, Experiment
        )


@pytest.mark.parametrize(
    "model",
    [
        Dataset,
        TestCase,
        Experiment,
        ExperimentResult,
        EvaluationResult,
        User,
        Organization,
    ],
)
async def test_no_principal_sees_nothing(
    engine: AsyncEngine, world: World, model: Any
) -> None:
    assert await _ids(engine, None, model) == set()


async def test_unknown_role_fails_closed(
    engine: AsyncEngine, world: World
) -> None:
    broken = Principal(
        world.principals["staff_a"].user_id,
        world.org_ids["a"],
        "owner",  # type: ignore[arg-type]
    )
    assert await _ids(engine, broken, Dataset) == set()
    assert await _ids(engine, broken, User) == set()


async def test_session_without_principal_in_info_fails_closed(
    engine: AsyncEngine, world: World
) -> None:
    # A scoped session whose info lost the principal sees nothing.
    async with scoped_sessionmaker(
        engine, world.principals["superadmin"]
    )() as session:
        session.sync_session.info.clear()
        assert await session.scalars(select(Dataset.id)) is not None
        assert set(await session.scalars(select(Dataset.id))) == set()


async def test_count_query_is_scoped(engine: AsyncEngine, world: World) -> None:
    # The exact pattern fastadmin uses for list totals.
    async with scoped_sessionmaker(
        engine, world.principals["staff_a"]
    )() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(select(Dataset).subquery())
            )
        ).scalar_one()
        plain = await session.scalar(select(func.count(Dataset.id)))
    assert total == 1
    assert plain == 1


async def test_session_get_is_scoped(engine: AsyncEngine, world: World) -> None:
    async with scoped_sessionmaker(
        engine, world.principals["staff_a"]
    )() as session:
        assert await session.get(Dataset, world.datasets["ds_b"]) is None
        assert await session.get(Dataset, world.datasets["ds_legacy"]) is None
        assert await session.get(Dataset, world.datasets["ds_c"]) is None
        own = await session.get(Dataset, world.datasets["ds_a"])
        assert own is not None
    async with scoped_sessionmaker(
        engine, world.principals["staff_a"]
    )() as session:
        assert (
            await session.get(User, world.principals["staff_b"].user_id) is None
        )
        assert await session.get(Organization, world.org_ids["a"]) is None


async def test_aliases_and_joins_are_scoped(
    engine: AsyncEngine, world: World
) -> None:
    alias = aliased(Dataset)
    async with scoped_sessionmaker(
        engine, world.principals["staff_a"]
    )() as session:
        ids = set(await session.scalars(select(alias.id)))
        joined = set(
            await session.scalars(
                select(TestCase.id).join(
                    Dataset, TestCase.dataset_id == Dataset.id
                )
            )
        )
    assert ids == {world.datasets["ds_a"]}
    assert joined == {world.cases["ds_a"]}


async def test_relationship_loads_are_scoped(
    engine: AsyncEngine, world: World
) -> None:
    # staff_a owns ds_a, but staff_b also has an experiment on it: a
    # relationship load from the visible dataset must not leak that one.
    from sqlalchemy.orm import relationship

    class DatasetWithExperiments(Dataset):
        experiments = relationship(
            Experiment,
            primaryjoin=Dataset.id == Experiment.dataset_id,
            foreign_keys=Experiment.dataset_id,
            viewonly=True,
        )

    async with scoped_sessionmaker(
        engine, world.principals["staff_a"]
    )() as session:
        rows = (
            await session.scalars(
                select(DatasetWithExperiments).options(
                    selectinload(DatasetWithExperiments.experiments)
                )
            )
        ).all()
        assert [r.id for r in rows] == [world.datasets["ds_a"]]
        assert {e.id for e in rows[0].experiments} == {
            world.experiments["exp_a"]
        }
    async with scoped_sessionmaker(
        engine, world.principals["admin_a"]
    )() as session:
        rows = (
            await session.scalars(
                select(DatasetWithExperiments)
                .where(Dataset.id == world.datasets["ds_a"])
                .options(selectinload(DatasetWithExperiments.experiments))
            )
        ).all()
        assert {e.id for e in rows[0].experiments} == _names(
            world.experiments, {"exp_a", "exp_b_on_a"}
        )


async def test_bulk_update_is_scoped(engine: AsyncEngine, world: World) -> None:
    async with scoped_sessionmaker(
        engine, world.principals["staff_a"]
    )() as session:
        await session.execute(update(Dataset).values(name="renamed"))
        await session.commit()
    async with scoped_sessionmaker(
        engine, world.principals["superadmin"]
    )() as session:
        names = set(await session.scalars(select(Dataset.name)))
    assert names == {"renamed", "ds_b", "ds_c", "ds_legacy"}


async def test_read_only_admin_denies_writes_and_scopes_sessions(
    engine: AsyncEngine,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("admin.core.get_engine", lambda: engine)
    admin = ReadOnlyScopedAdmin(Dataset)
    for permission in (
        admin.has_add_permission,
        admin.has_change_permission,
        admin.has_delete_permission,
        admin.has_export_permission,
        admin.has_action_permission,
    ):
        assert await permission() is False
        assert await permission(uuid.uuid4()) is False

    # The fastadmin list path (orm_get_list) goes through get_sessionmaker.
    rows, total = await admin.orm_get_list(offset=0, limit=10)
    assert (rows, total) == ([], 0)  # no user bound: fail closed

    admin.set_context(user={"id": "not-a-principal"})
    assert await admin.orm_get_list(offset=0, limit=10) == ([], 0)

    # A bare Principal is not what the user admin binds: no rows either.
    admin.set_context(user=world.principals["staff_a"])
    assert await admin.orm_get_list(offset=0, limit=10) == ([], 0)

    admin.set_context(user=SessionUser(world.principals["staff_a"]))
    rows, total = await admin.orm_get_list(offset=0, limit=10)
    assert [r.id for r in rows] == [world.datasets["ds_a"]]
    assert total == 1
    assert await admin.orm_get_obj(world.datasets["ds_b"]) is None
    got = await admin.orm_get_obj(world.datasets["ds_a"])
    assert got is not None

    admin.set_context(user=SessionUser(world.principals["admin_a"]))
    rows, total = await admin.orm_get_list(offset=0, limit=10)
    assert total == 3
