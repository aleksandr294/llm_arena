"""Scoped database access for the admin panel.

fastadmin has no row-level security: its admin classes read and write through
whatever sessionmaker they are given. Every session handed out here carries
the signed-in user's ``Principal`` and a ``do_orm_execute`` hook adds the
same visibility rules as the REST API to every ORM statement, so a new admin
class cannot forget to scope its queries. Without an (active) principal every
scoped model matches no rows.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastadmin import AdminApiException, SqlAlchemyModelAdmin
from sqlalchemy import ColumnElement, event, false, select, true
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from components.core.access import scope_clause
from components.core.database import get_engine
from components.core.enums import Role
from components.core.schemas import Principal
from components.datasets.models import Dataset, TestCase
from components.evaluation.models import EvaluationResult
from components.experiments.models import Experiment, ExperimentResult
from components.organizations.models import Organization
from components.users.models import User

PRINCIPAL_KEY = "principal"


class SessionUser:
    """What the user admin returns while fastadmin validates a session.

    fastadmin binds the value as ``self.user``. It is deliberately not a
    serialisable type (no ``__dict__``, not a model or dataclass): were it
    ever returned from a data route, serialising it fails instead of
    exposing the principal's ids and role.
    """

    __slots__ = ("principal",)

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    def __repr__(self) -> str:
        return "SessionUser()"


def principal_of(user: Any) -> Principal | None:
    """The principal behind the value fastadmin bound as ``self.user``."""
    if isinstance(user, SessionUser):
        return user.principal
    return None


class ScopedSession(Session):
    """Sync session class behind the scoped ``AsyncSession``. The scoping
    hook is registered on this class only, so other sessions are unaffected."""


def _owned_by(model: Any, fk: Any, parent: Any, principal: Principal) -> Any:
    """Rows of ``model`` whose parent row is visible to ``principal``."""
    return fk.in_(select(parent.id).where(scope_clause(parent, principal)))


def _user_clause(principal: Principal) -> ColumnElement[bool]:
    match principal.role:
        case Role.STAFF:
            return User.id == principal.user_id
        case Role.ADMIN:
            if principal.organization_id is None:
                return false()
            return (User.organization_id == principal.organization_id) & (
                User.role != Role.SUPERADMIN.value
            )
        case Role.SUPERADMIN:
            return true()
    return false()


def _organization_clause(principal: Principal) -> ColumnElement[bool]:
    # Organizations are platform-level data: only superadmins see them.
    return true() if principal.role is Role.SUPERADMIN else false()


def _clauses(principal: Principal) -> dict[type, ColumnElement[bool]]:
    return {
        Dataset: scope_clause(Dataset, principal),
        Experiment: scope_clause(Experiment, principal),
        TestCase: _owned_by(TestCase, TestCase.dataset_id, Dataset, principal),
        ExperimentResult: _owned_by(
            ExperimentResult,
            ExperimentResult.experiment_id,
            Experiment,
            principal,
        ),
        EvaluationResult: _owned_by(
            EvaluationResult,
            EvaluationResult.experiment_id,
            Experiment,
            principal,
        ),
        User: _user_clause(principal),
        Organization: _organization_clause(principal),
    }


SCOPED_MODELS: tuple[type, ...] = (
    Dataset,
    TestCase,
    Experiment,
    ExperimentResult,
    EvaluationResult,
    User,
    Organization,
)


def criteria_for(principal: Principal | None) -> list[Any]:
    """One ``with_loader_criteria`` option per scoped model. No principal, or
    a role that cannot be resolved, matches nothing (fail closed)."""
    clauses: dict[type, ColumnElement[bool]]
    try:
        clauses = (
            {m: false() for m in SCOPED_MODELS}
            if principal is None
            else _clauses(principal)
        )
    except ValueError:
        clauses = {m: false() for m in SCOPED_MODELS}
    return [
        with_loader_criteria(model, clause, include_aliases=True)
        for model, clause in clauses.items()
    ]


@event.listens_for(ScopedSession, "do_orm_execute")
def _apply_scope(state: ORMExecuteState) -> None:
    # Column loads refresh rows that were already visible when loaded.
    if state.is_column_load or not (
        state.is_select or state.is_update or state.is_delete
    ):
        return
    principal = state.session.info.get(PRINCIPAL_KEY)
    if not isinstance(principal, Principal):
        principal = None
    state.statement = state.statement.options(*criteria_for(principal))


def scoped_sessionmaker(
    bind: AsyncEngine, principal: Principal | None
) -> async_sessionmaker[AsyncSession]:
    """A sessionmaker whose sessions only see what ``principal`` may see."""
    return async_sessionmaker(
        bind,
        sync_session_class=ScopedSession,
        expire_on_commit=False,
        info={PRINCIPAL_KEY: principal},
    )


COMPARISONS = frozenset({"exact", "in", "lt", "lte", "gt", "gte"})


def _coerce_filter_value(column: Any, value: Any) -> Any:
    """Turn one filter value into the column's Python type, or raise
    ``ValueError``. Without this a malformed value reaches the driver, whose
    error message quotes the value and ends up in the server log."""
    if value is None:
        return None
    if isinstance(value, bool) and column.type.python_type is not bool:
        raise ValueError("unsupported value")
    match column.type.python_type:
        case type() as kind if kind is uuid.UUID:
            return uuid.UUID(str(value))
        case type() as kind if kind is datetime:
            parsed = datetime.fromisoformat(str(value))
            if getattr(column.type, "timezone", False):
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).replace(tzinfo=None)
        case type() as kind if kind is bool:
            if isinstance(value, bool):
                return value
            lowered = str(value).lower()
            if lowered in ("true", "1"):
                return True
            if lowered in ("false", "0"):
                return False
            raise ValueError("not a boolean")
        case type() as kind if kind is int:
            return int(value)
        case type() as kind if kind is float:
            return float(value)
        case type() as kind if kind is str:
            if not isinstance(value, str):
                raise ValueError("not a string")
            return value
    raise ValueError("unsupported column type")


class ReadOnlyScopedAdmin(SqlAlchemyModelAdmin):  # type: ignore[misc]
    """Base for every panel model: scoped reads, no writes or exports.

    fastadmin binds the signed-in user to ``self.user`` per request. The User
    admin must make that value a ``SessionUser`` (see ``get_obj`` of the user
    admin); anything else leaves the session without a principal, which
    sees no rows.
    """

    list_per_page = 25

    async def serialize_obj(
        self, obj: Any, list_view: bool = False
    ) -> dict[str, Any]:
        """fastadmin serializes every column in list responses and only uses
        ``list_display`` to pick the table columns. Restricting list rows to
        ``list_display`` is what keeps bulky text (responses, prompts, JSON
        configuration) out of lists: it appears in detail views only."""
        data: dict[str, Any] = await super().serialize_obj(
            obj, list_view=list_view
        )
        # The default ``__str__`` of a model is its module path and memory
        # address; show the model name and a short id instead.
        pk = self.get_model_pk_name(self.model_cls)
        data["__str__"] = f"{self.model_cls.__name__} {str(data.get(pk))[:8]}"
        if not list_view or not self.list_display:
            return data
        keep = {*self.list_display, pk, "__str__"}
        return {key: value for key, value in data.items() if key in keep}

    async def orm_get_list(
        self,
        offset: int | None = None,
        limit: int | None = None,
        search: str | None = None,
        sort_by: str | None = None,
        filters: dict[Any, Any] | None = None,
    ) -> tuple[list[Any], int]:
        """Reject filter values the column cannot hold with a 422 before
        they reach the database (a driver error would echo the value into
        the log and answer 500)."""
        checked: dict[Any, Any] = {}
        for (field, condition), value in (filters or {}).items():
            column = getattr(self.model_cls, field, None)
            if condition not in COMPARISONS or not hasattr(column, "type"):
                checked[(field, condition)] = value  # substring or relation
                continue
            try:
                if condition == "in":
                    if not isinstance(value, list | tuple):
                        raise ValueError("expected a list")
                    value = [_coerce_filter_value(column, v) for v in value]
                else:
                    value = _coerce_filter_value(column, value)
            except (ValueError, TypeError, NotImplementedError, OverflowError):
                raise AdminApiException(
                    422, detail=f"Invalid value for filter {field}."
                ) from None
            checked[(field, condition)] = value
        result: tuple[list[Any], int] = await super().orm_get_list(
            offset=offset,
            limit=limit,
            search=search,
            sort_by=sort_by,
            filters=checked or None,
        )
        return result

    async def orm_get_obj(self, id: Any) -> Any | None:
        # A malformed id is "not found" (404), not a database error (500).
        try:
            key = id if isinstance(id, uuid.UUID) else uuid.UUID(str(id))
        except ValueError:
            return None
        return await super().orm_get_obj(key)

    def get_sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return scoped_sessionmaker(get_engine(), principal_of(self.user))

    async def upload_file(self, *args: Any, **kwargs: Any) -> str:
        raise AdminApiException(403, detail="Uploads are not allowed.")

    async def has_add_permission(self, user_id: Any = None) -> bool:
        return False

    async def has_change_permission(self, user_id: Any = None) -> bool:
        return False

    async def has_delete_permission(self, user_id: Any = None) -> bool:
        return False

    async def has_export_permission(self, user_id: Any = None) -> bool:
        return False

    async def has_action_permission(self, user_id: Any = None) -> bool:
        return False
