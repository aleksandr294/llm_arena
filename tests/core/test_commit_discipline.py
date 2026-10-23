"""One commit per operation, committed before the response, no stray commits.

The first group needs no database. The ``integration`` group runs the real
endpoints against PostgreSQL (``DATABASE_URL``).
"""

import ast
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.utils import SessionDep
from components.auth.dependencies import build_auth_service
from components.auth.exceptions import InvalidRefreshTokenError
from components.core.config import SettingsSchema
from components.core.database import get_session, session_scope
from components.core.enums import Role
from components.datasets.dependencies import build_dataset_service
from components.datasets.schemas import DatasetCreateSchema
from components.experiments.dependencies import build_experiment_service
from components.experiments.enums import ExperimentStatus
from components.experiments.models import Experiment
from components.organizations.dependencies import build_organization_service
from components.organizations.models import Organization
from components.users.dependencies import build_user_service
from tests.access import PASSWORD, ROOT, Actors, Factory
from tests.experiments.helpers import (
    Enqueued,
    FakeLLM,
    create_data,
    make_dataset,
)

ROOT_DIR = Path(__file__).resolve().parents[2]

# Modules that may call ``.commit()``. Everything else goes through
# ``commit(session)`` or ``session_scope`` from ``components.core.database``.
_COMMIT_ALLOWLIST = {"components/core/database.py"}
_SKIPPED_DIRS = {"tests", "migrations", "scripts", ".venv", "node_modules"}


def _source_files() -> Iterator[Path]:
    for path in sorted(ROOT_DIR.rglob("*.py")):
        relative = path.relative_to(ROOT_DIR)
        if relative.parts[0] in _SKIPPED_DIRS or any(
            part.startswith(".") for part in relative.parts
        ):
            continue
        yield path


def test_no_direct_session_commit_outside_database_module() -> None:
    offenders: list[str] = []
    for path in _source_files():
        relative = path.relative_to(ROOT_DIR).as_posix()
        if relative in _COMMIT_ALLOWLIST:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"
            ):
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == []


def _raw_session_dependencies(source: str) -> list[int]:
    """Line numbers of ``Depends(get_session...)`` calls."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "get_session"
    ]


def test_endpoints_use_the_shared_session_alias() -> None:
    """A raw ``Depends(get_session)`` would get the default scope, whose
    commit runs after the response. Only the alias in ``api/v1/utils.py``
    (with ``scope="function"``) may name ``get_session``."""
    offenders = []
    for path in sorted((ROOT_DIR / "api").rglob("*.py")):
        relative = path.relative_to(ROOT_DIR).as_posix()
        if relative == "api/v1/utils.py":
            continue
        lines = _raw_session_dependencies(path.read_text(encoding="utf-8"))
        offenders += [f"{relative}:{line}" for line in lines]
    assert offenders == []


def test_raw_session_dependency_checker_flags_a_violation() -> None:
    bad = (
        "from fastapi import Depends\n"
        "async def endpoint(s=Depends(get_session)):\n"
        "    pass\n"
    )
    assert _raw_session_dependencies(bad) == [2]
    good = "async def endpoint(s: SessionDep):\n    pass\n"
    assert _raw_session_dependencies(good) == []


# --- the session dependency finishes before the response ---------------------


class _Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []


class _FakeSession:
    def __init__(self, recorder: _Recorder, fail_commit: bool = False) -> None:
        self.info: dict[str, Any] = {}
        self._recorder = recorder
        self._fail_commit = fail_commit

    async def commit(self) -> None:
        self._recorder.events.append("commit")
        if self._fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self._recorder.events.append("rollback")

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._recorder.events.append("close")


async def _asgi(
    app: FastAPI,
    recorder: _Recorder,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> int:
    """Drive the app at ASGI level, recording ``response.start`` and
    ``response.body`` next to the other events. Returns the status."""
    raw_headers = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]
    raw_headers += [(b"content-length", str(len(body)).encode())]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
    }
    status: list[int] = []
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
            recorder.events.append("response.start")

    # The server error middleware sends the 500 and then re-raises.
    with contextlib.suppress(Exception):
        await app(scope, receive, send)  # type: ignore[arg-type]
    return status[0]


def _local_app(recorder: _Recorder, fail_commit: bool = False) -> FastAPI:
    app = FastAPI()

    async def override() -> AsyncIterator[AsyncSession]:
        factory: Any = lambda: _FakeSession(recorder, fail_commit)  # noqa: E731
        async with session_scope(factory) as session:
            yield session

    app.dependency_overrides[get_session] = override

    @app.post("/write")
    async def write(session: SessionDep) -> dict[str, bool]:
        recorder.events.append("handler")
        return {"ok": True}

    return app


async def test_commit_runs_before_the_response_starts() -> None:
    recorder = _Recorder()
    status = await _asgi(_local_app(recorder), recorder, "POST", "/write")
    assert status == 200
    assert recorder.events == ["handler", "commit", "close", "response.start"]


async def test_failed_commit_is_an_error_response() -> None:
    recorder = _Recorder()
    app = _local_app(recorder, fail_commit=True)
    status = await _asgi(app, recorder, "POST", "/write")
    assert status == 500
    assert recorder.events.index("commit") < recorder.events.index(
        "response.start"
    )
    assert "rollback" in recorder.events


def test_session_alias_has_function_scope() -> None:
    (marker,) = SessionDep.__metadata__  # type: ignore[attr-defined]
    assert marker.dependency is get_session
    assert marker.scope == "function"


# --- real endpoints and services (PostgreSQL) -------------------------------


class _CommitLog:
    """Records ``AsyncSession.commit`` calls next to the other events."""

    def __init__(self) -> None:
        self.recorder = _Recorder()

    @property
    def commits(self) -> int:
        return self.recorder.events.count("commit")


@pytest.fixture
def commit_log(monkeypatch: pytest.MonkeyPatch) -> _CommitLog:
    log = _CommitLog()
    original = AsyncSession.commit

    async def recording(self: AsyncSession) -> None:
        log.recorder.events.append("commit")
        await original(self)

    monkeypatch.setattr(AsyncSession, "commit", recording)
    return log


@pytest.mark.integration
async def test_write_endpoint_commits_before_the_response(
    app: FastAPI, actors: Actors, commit_log: _CommitLog
) -> None:
    headers = await actors.headers("staff_a")
    commit_log.recorder.events.clear()
    status = await _asgi(
        app,
        commit_log.recorder,
        "POST",
        "/api/v1/datasets",
        {**headers, "content-type": "application/json"},
        b'{"name": "d"}',
    )
    assert status == 201
    events = commit_log.recorder.events
    assert events.count("commit") == 1
    assert events.index("commit") < events.index("response.start")


@pytest.mark.integration
async def test_failed_commit_on_a_write_endpoint_is_a_500(
    app: FastAPI,
    actors: Actors,
    session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await actors.headers("staff_a")
    recorder = _Recorder()

    async def failing(self: AsyncSession) -> None:
        recorder.events.append("commit")
        await self.rollback()
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", failing)
    status = await _asgi(
        app,
        recorder,
        "POST",
        "/api/v1/datasets",
        {**headers, "content-type": "application/json"},
        b'{"name": "never stored"}',
    )
    assert status == 500
    async with session_factory() as session:
        from components.datasets.models import Dataset

        assert (await session.scalars(select(Dataset))).all() == []


@pytest.mark.integration
async def test_failed_enqueue_is_an_error_but_data_is_committed(
    app: FastAPI,
    actors: Actors,
    session_factory: Factory,
) -> None:
    from api.v1.endpoints.experiments import get_enqueue, get_llm_service

    owner = actors.principal("staff_a")
    dataset_id = await make_dataset(session_factory, 3, owner)
    async with session_scope(session_factory) as session:
        experiment = await build_experiment_service(
            session, FakeLLM().as_service(), Enqueued()
        ).create(owner, create_data(dataset_id))
        experiment_id = experiment.id

    async def broker_down(ids: Any) -> None:
        raise ConnectionError("broker down")

    app.dependency_overrides[get_llm_service] = FakeLLM().as_service
    app.dependency_overrides[get_enqueue] = lambda: broker_down
    headers = await actors.headers("staff_a")
    recorder = _Recorder()
    status = await _asgi(
        app,
        recorder,
        "POST",
        f"/api/v1/experiments/{experiment_id}/start",
        headers,
    )
    assert status == 500

    async with session_factory() as session:
        stored = await session.get(Experiment, experiment_id)
        assert stored is not None
        assert stored.status == ExperimentStatus.RUNNING

    enqueued = Enqueued()
    app.dependency_overrides[get_enqueue] = lambda: enqueued
    client = await actors.client("staff_a")
    response = await client.post(f"/api/v1/experiments/{experiment_id}/start")
    assert response.status_code == 202
    assert len(enqueued.flat) == 3


@pytest.mark.integration
async def test_start_commits_once_and_enqueues_after_the_commit(
    session_factory: Factory,
    actors: Actors,
    commit_log: _CommitLog,
) -> None:
    owner = actors.principal("staff_a")
    dataset_id = await make_dataset(session_factory, 2, owner)
    async with session_scope(session_factory) as session:
        experiment = await build_experiment_service(
            session, FakeLLM().as_service(), Enqueued()
        ).create(owner, create_data(dataset_id))
        experiment_id = experiment.id
    commit_log.recorder.events.clear()
    seen: list[str] = []

    async def enqueue(ids: Any) -> None:
        commit_log.recorder.events.append("enqueue")
        # A worker on another connection must already see the new state.
        async with session_factory() as other:
            row = await other.get(Experiment, experiment_id)
            assert row is not None
            seen.append(row.status)

    async with session_factory() as session:
        service = build_experiment_service(
            session, FakeLLM().as_service(), enqueue
        )
        assert await service.start(ROOT, experiment_id) == 2
    assert commit_log.recorder.events == ["commit", "enqueue"]
    assert seen == ["running"]


async def _count_commits(
    log: _CommitLog,
    operation: Callable[[AsyncSession], Awaitable[Any]],
    factory: Factory,
) -> int:
    log.recorder.events.clear()
    async with session_scope(factory) as session:
        await operation(session)
    return log.commits


@pytest.mark.integration
async def test_one_commit_per_write_operation(
    session_factory: Factory,
    actors: Actors,
    settings: SettingsSchema,
    commit_log: _CommitLog,
) -> None:
    staff = actors.principal("staff_a")
    admin = actors.principal("admin")
    superadmin = actors.principal("superadmin")

    async def create_dataset(session: AsyncSession) -> None:
        await build_dataset_service(session).create_dataset(
            staff, DatasetCreateSchema(name="d")
        )

    async def create_organization(session: AsyncSession) -> None:
        await build_organization_service(session).create("new organization")

    async def create_user(session: AsyncSession) -> None:
        auth = build_auth_service(session, settings)
        await build_user_service(session, auth).create_user(
            admin, "new@example.com", PASSWORD, Role.STAFF, None
        )

    async def update_user(session: AsyncSession) -> None:
        auth = build_auth_service(session, settings)
        await build_user_service(session, auth).update_user(
            admin, actors["staff_b"].user_id, is_active=False
        )

    async def login(session: AsyncSession) -> None:
        await build_auth_service(session, settings).login(
            actors["staff_a"].email, PASSWORD
        )

    async def update_organization(session: AsyncSession) -> None:
        await build_organization_service(session).update(
            actors.org_id, is_active=True
        )

    async def create_superadmin(session: AsyncSession) -> None:
        auth = build_auth_service(session, settings)
        await build_user_service(session, auth).create_superadmin(
            "root@example.com", PASSWORD
        )

    operations = [
        create_dataset,
        create_organization,
        create_user,
        update_user,
        login,
        update_organization,
        create_superadmin,
    ]
    for operation in operations:
        count = await _count_commits(commit_log, operation, session_factory)
        assert count == 1, operation.__name__
    assert superadmin.role is Role.SUPERADMIN


@pytest.mark.integration
async def test_refresh_reuse_persists_the_revocation_then_rejects(
    session_factory: Factory,
    actors: Actors,
    settings: SettingsSchema,
    commit_log: _CommitLog,
) -> None:
    async with session_scope(session_factory) as session:
        pair = await build_auth_service(session, settings).login(
            actors["staff_a"].email, PASSWORD
        )
    async with session_scope(session_factory) as session:
        await build_auth_service(session, settings).refresh(pair.refresh_token)

    commit_log.recorder.events.clear()
    with pytest.raises(InvalidRefreshTokenError):
        async with session_scope(session_factory) as session:
            await build_auth_service(session, settings).refresh(
                pair.refresh_token
            )
    # The service commits once before raising; the scope then rolls back.
    assert commit_log.commits == 1

    # The family is revoked durably: the token issued by the first refresh
    # no longer works either.
    async with session_factory() as session:
        from components.auth.models import RefreshToken

        rows = (await session.scalars(select(RefreshToken))).all()
        assert rows and all(row.revoked_at is not None for row in rows)


@pytest.mark.integration
async def test_organization_service_sees_no_commit_without_a_scope(
    session_factory: Factory,
) -> None:
    """Services never commit: without the entry point's scope nothing is
    stored."""
    async with session_factory() as session:
        await build_organization_service(session).create("not committed")
    async with session_factory() as session:
        names = (await session.scalars(select(Organization.name))).all()
    assert "not committed" not in names
