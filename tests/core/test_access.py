import uuid

import pytest
from sqlalchemy import ColumnElement, select
from sqlalchemy.dialects import postgresql

from components.core.access import (
    OrganizationRequiredError,
    Principal,
    Role,
    require_organization,
    scope_clause,
)
from components.datasets.models import Dataset
from components.experiments.models import Experiment

USER = uuid.uuid4()
ORG = uuid.uuid4()


def _sql(clause: ColumnElement[bool], model: type) -> str:
    stmt = select(model.id).where(clause)  # type: ignore[attr-defined]
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.parametrize("model", [Dataset, Experiment])
def test_staff_scoped_to_owner(model: type) -> None:
    sql = _sql(scope_clause(model, Principal(USER, ORG, Role.STAFF)), model)
    assert f"owner_id = '{USER}'" in sql
    assert "organization_id" not in sql


@pytest.mark.parametrize("model", [Dataset, Experiment])
def test_admin_scoped_to_organization(model: type) -> None:
    sql = _sql(scope_clause(model, Principal(USER, ORG, Role.ADMIN)), model)
    assert f"organization_id = '{ORG}'" in sql
    assert "owner_id" not in sql


@pytest.mark.parametrize("model", [Dataset, Experiment])
def test_superadmin_has_no_condition(model: type) -> None:
    sql = _sql(
        scope_clause(model, Principal(USER, None, Role.SUPERADMIN)), model
    )
    assert "owner_id" not in sql and "organization_id" not in sql
    assert "WHERE true" in sql


def test_admin_without_organization_matches_nothing() -> None:
    sql = _sql(
        scope_clause(Dataset, Principal(USER, None, Role.ADMIN)), Dataset
    )
    assert "WHERE false" in sql


def test_require_organization() -> None:
    assert require_organization(Principal(USER, ORG, Role.STAFF)) == ORG
    with pytest.raises(OrganizationRequiredError, match="superadmin"):
        require_organization(Principal(USER, None, Role.SUPERADMIN))
