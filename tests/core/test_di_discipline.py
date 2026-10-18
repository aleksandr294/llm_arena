"""Services receive their collaborators; they never build them.

No database needed: these run in the fast (non-integration) set.
"""

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from components.auth.service import AuthService
from components.experiments.service import ExperimentService
from components.third_party.llm.service import LLMService
from components.users.service import UserService

ROOT_DIR = Path(__file__).resolve().parents[2]

# Entry points that legitimately build settings, factories and scopes (the
# grep results of the DI review): API wiring, CLI, admin, tasks, main.
_ENTRY_POINTS = {
    "api/app.py",
    "api/v1/utils.py",
    "api/v1/endpoints/experiments.py",
    "api/v1/endpoints/metrics.py",
    "admin/users/admin.py",
    "admin/organizations/admin.py",
    "cli.py",
    "main.py",
    "taskiq_app.py",
    "components/core/database.py",
    "components/core/health.py",
    "components/experiments/tasks.py",
    "components/evaluation/tasks.py",
}
_FORBIDDEN = {"get_settings", "get_session_factory", "session_scope"}
_BUSINESS_FILES = (
    "service.py",
    "repository.py",
    "dependencies.py",
    "runner.py",
)


def _called_names(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


def test_business_modules_do_not_build_settings_or_sessions() -> None:
    offenders = []
    for path in sorted((ROOT_DIR / "components").rglob("*.py")):
        relative = path.relative_to(ROOT_DIR).as_posix()
        if relative in _ENTRY_POINTS or path.name not in _BUSINESS_FILES:
            continue
        found = _FORBIDDEN.intersection(_called_names(path))
        if found:
            offenders.append(f"{relative}: {sorted(found)}")
    assert offenders == []


@pytest.mark.parametrize(
    ("service", "required"),
    [
        (UserService, {"session", "auth_service"}),
        (ExperimentService, {"session", "llm_service", "enqueue"}),
        (AuthService, {"session"}),
        (LLMService, {"settings"}),
    ],
)
def test_collaborators_are_required_constructor_arguments(
    service: Any, required: set[str]
) -> None:
    parameters = inspect.signature(service).parameters
    for name in required:
        assert name in parameters, name
        assert parameters[name].default is inspect.Parameter.empty, name


def test_user_service_cannot_be_built_without_its_auth_service() -> None:
    with pytest.raises(TypeError):
        UserService(object())  # type: ignore[call-arg, arg-type]


def test_experiment_service_cannot_be_built_without_collaborators() -> None:
    with pytest.raises(TypeError):
        ExperimentService(object())  # type: ignore[call-arg, arg-type]
    with pytest.raises(TypeError):
        ExperimentService(object(), object())  # type: ignore[call-arg, arg-type]


def test_llm_service_cannot_be_built_without_settings() -> None:
    with pytest.raises(TypeError):
        LLMService()  # type: ignore[call-arg]
