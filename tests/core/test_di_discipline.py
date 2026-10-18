"""Services receive their collaborators; they never build them.

No database needed: these run in the fast (non-integration) set.
"""

import ast
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

from components.auth.service import AuthService
from components.experiments.service import ExperimentService
from components.third_party.llm.service import LLMService
from components.users.service import UserService

ROOT_DIR = Path(__file__).resolve().parents[2]

# Entry and composition modules that may read settings, session factories or
# open a session scope. Every other module under components/ and admin/ must
# receive these from its caller. Each entry must really use a forbidden name
# (checked below), so the list cannot keep dead entries.
_ENTRY_POINTS = {
    # defines the cached settings accessor and builds SettingsSchema()
    "components/core/config.py",
    # defines the engine, session factory and session_scope
    "components/core/database.py",
    # readiness probe: reads the broker URL from settings
    "components/core/health.py",
    # Taskiq task functions: the task entry point opens the session scope
    "components/experiments/tasks.py",
    "components/evaluation/tasks.py",
    # fastadmin handlers: the panel's entry points open their own scope
    "admin/users/admin.py",
    "admin/organizations/admin.py",
}
_FORBIDDEN = {
    "get_settings",
    "get_session_factory",
    "session_scope",
    "SettingsSchema",
}
# Modules holding business logic: they must not build other services.
_SERVICE_MODULES = ("service.py", "runner.py")
# ``XRepository(session)`` / ``XRepository(self.session)`` is allowed: the
# service builds its repositories from the session it was given.
_SESSION_ARGUMENTS = {"session", "self.session", "self._session"}
_LEAF_NAMES = {"exceptions", "enums"}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _violations(source: str, service_module: bool) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None:
            continue
        if name in _FORBIDDEN:
            found.append(f"{name} (line {node.lineno})")
        elif service_module and name.endswith("Service"):
            found.append(f"{name}( (line {node.lineno})")
        elif service_module and name.endswith("Repository"):
            args = [ast.unparse(arg) for arg in node.args]
            if args == [] or not set(args) <= _SESSION_ARGUMENTS:
                found.append(f"{name}({', '.join(args)}) (line {node.lineno})")
    return found


def _production_modules() -> list[Path]:
    return sorted(
        path
        for top in ("components", "admin")
        for path in (ROOT_DIR / top).rglob("*.py")
    )


def _relative(path: Path) -> str:
    return path.relative_to(ROOT_DIR).as_posix()


def test_business_modules_do_not_build_settings_sessions_or_services() -> None:
    offenders = []
    for path in _production_modules():
        relative = _relative(path)
        if relative in _ENTRY_POINTS:
            continue
        found = _violations(
            path.read_text(encoding="utf-8"),
            service_module=path.name in _SERVICE_MODULES,
        )
        if found:
            offenders.append(f"{relative}: {found}")
    assert offenders == []


def test_entry_point_allowlist_has_no_dead_entries() -> None:
    for relative in sorted(_ENTRY_POINTS):
        path = ROOT_DIR / relative
        assert path.is_file(), relative
        used = {
            name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and (name := _call_name(node)) is not None
        }
        # config.py only defines get_settings; it builds SettingsSchema().
        assert _FORBIDDEN & used, relative


def test_checker_flags_deliberate_violations() -> None:
    bad = (
        "def f(self, session):\n"
        "    settings = get_settings()\n"
        "    factory = get_session_factory()\n"
        "    with session_scope():\n"
        "        pass\n"
        "    SettingsSchema()\n"
        "    OtherService(session)\n"
        "    UserRepository(make_session())\n"
        "    UserRepository()\n"
    )
    assert len(_violations(bad, service_module=True)) == 7
    # Outside service modules only the settings/session names are checked.
    assert len(_violations(bad, service_module=False)) == 4
    allowed = (
        "def f(self, session):\n"
        "    UserRepository(session)\n"
        "    UserRepository(self.session)\n"
        "    UserRepository(self._session)\n"
    )
    assert _violations(allowed, service_module=True) == []


# --- leaf modules (CLAUDE.md): exceptions.py and enums.py ------------------


def _leaf_import_violations(source: str) -> list[str]:
    """Imports other than the standard library or another leaf module
    (``components.*.exceptions`` / ``components.*.enums``)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = ["." * node.level + (node.module or "")]
        else:
            continue
        for module in modules:
            top = module.split(".")[0]
            if top in sys.stdlib_module_names:
                continue
            parts = module.split(".")
            if (
                parts[0] == "components"
                and len(parts) > 1
                and parts[-1] in _LEAF_NAMES
            ):
                continue
            found.append(module)
    return found


def test_leaf_modules_import_only_stdlib_and_other_leaf_modules() -> None:
    leaves = [
        path for path in _production_modules() if path.stem in _LEAF_NAMES
    ]
    assert leaves  # the rule is not vacuous
    offenders = {}
    for path in leaves:
        found = _leaf_import_violations(path.read_text(encoding="utf-8"))
        if found:
            offenders[_relative(path)] = found
    assert offenders == {}


def test_leaf_checker_flags_deliberate_violations() -> None:
    bad = (
        "import uuid\n"
        "from components.users.service import UserService\n"
        "from components.core.schemas import Principal\n"
        "from pydantic import BaseModel\n"
        "from . import enums\n"
    )
    assert _leaf_import_violations(bad) == [
        "components.users.service",
        "components.core.schemas",
        "pydantic",
        ".",
    ]
    ok = "import enum\nfrom components.core.enums import Role\n"
    assert _leaf_import_violations(ok) == []


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
