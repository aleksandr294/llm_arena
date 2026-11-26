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
from typing import Any, ClassVar

from fastadmin import AdminApiException, SqlAlchemyModelAdmin
from fastadmin.models.schemas import ModelFieldWidgetSchema, WidgetType
from sqlalchemy import ColumnElement, event, false, select, true
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from admin.schemas import SessionUser
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


def principal_of(user: Any) -> Principal | None:
    """The principal behind the value fastadmin bound as ``self.user``."""
    if isinstance(user, SessionUser):
        return user.principal
    return None


FORBIDDEN_DETAIL = "You do not have permission to perform this action."


def forbidden() -> AdminApiException:
    return AdminApiException(403, detail=FORBIDDEN_DETAIL)


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

    # Read-only values computed for the detail view only: key -> widget
    # showing it. fastadmin builds forms and detail pages from model columns,
    # so each key is registered as a synthetic, read-only form field (see
    # ``get_model_fields_with_widget_types``); it is never writable and never
    # part of a list payload. Keys must not collide with a column or method.
    computed_fields: ClassVar[dict[str, WidgetType]] = {}
    # Form-only keys that are not queryable columns (e.g. a password input).
    form_only_fields: ClassVar[frozenset[str]] = frozenset()

    async def computed_values(
        self, session: AsyncSession, obj: Any
    ) -> dict[str, Any]:
        """Values for ``computed_fields`` of one row. ``session`` is scoped
        to the signed-in user, so a related row the user may not see yields
        no value (``None``/``0``). Never return secrets or hashes."""
        return {}

    def get_model_fields_with_widget_types(
        self, with_m2m: bool | None = None
    ) -> list[ModelFieldWidgetSchema]:
        fields: list[ModelFieldWidgetSchema] = (
            super().get_model_fields_with_widget_types(with_m2m=with_m2m)
        )
        if with_m2m:  # only many-to-many fields were asked for
            return fields
        return [
            *fields,
            *(
                ModelFieldWidgetSchema(
                    name=key,
                    column_name=key,
                    is_m2m=False,
                    is_pk=False,
                    is_immutable=False,
                    form_widget_type=widget,
                    form_widget_props={
                        "required": False,
                        "disabled": True,
                        "readOnly": True,
                    },
                    filter_widget_type=WidgetType.Input,
                    filter_widget_props={"required": False},
                )
                for key, widget in self.computed_fields.items()
            ),
        ]

    def get_fields_for_serialize(self) -> set[str]:
        # Added after ``exclude`` on purpose: computed keys are not columns.
        names: set[str] = super().get_fields_for_serialize()
        return names | set(self.computed_fields)

    def get_writable_field_names(self) -> set[str]:
        names: set[str] = super().get_writable_field_names()
        return names - set(self.computed_fields)

    async def serialize_obj_attributes(
        self,
        obj: Any,
        attributes_to_serizalize: list[ModelFieldWidgetSchema],
        list_view: bool = False,
    ) -> dict[str, Any]:
        # Computed keys are not attributes of the row.
        columns = [
            f
            for f in attributes_to_serizalize
            if f.name not in self.computed_fields
        ]
        data: dict[str, Any] = await super().serialize_obj_attributes(
            obj, columns, list_view=list_view
        )
        return data

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
        if not list_view and self.computed_fields:
            async with self.get_sessionmaker()() as session:
                values = await self.computed_values(session, obj)
            for key in self.computed_fields:
                data[key] = values.get(key)
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
        the log and answer 500). Computed and form-only keys are registered
        as form fields but are not columns: refuse them as filter or sort
        keys (422)."""
        not_queryable = {*self.computed_fields, *self.form_only_fields}
        keys = {field for field, _ in (filters or {})}
        keys.add((sort_by or "").lstrip("-"))
        if keys & not_queryable:
            raise AdminApiException(422, detail="Invalid filter or sort key.")
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

    def caller(self) -> Principal:
        """The signed-in principal, or 403: write paths never run without
        one, whatever the permission hooks said."""
        principal = principal_of(self.user)
        if principal is None:
            raise forbidden()
        return principal

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
