"""Static checks of docker-compose.yml: settings reach the containers,
required variables are guarded, data is persistent, the worker is probed."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from components.core.config import SettingsSchema

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Settings the compose file deliberately does not pass, with the reason.
NOT_PASSED = {
    # The container always listens on 8000, and the published port is fixed.
    "API_PORT",
}
# Only the api service listens for HTTP (it sets API_HOST itself).
WORKER_NOT_PASSED = NOT_PASSED | {"API_HOST"}
REQUIRED = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "RABBITMQ_DEFAULT_USER",
    "RABBITMQ_DEFAULT_PASS",
    "JWT_SECRET_KEY",
}


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    loaded = yaml.safe_load(COMPOSE.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _environment(compose: dict[str, Any], service: str) -> dict[str, str]:
    env = compose["services"][service]["environment"]
    return {key: str(value) for key, value in env.items()}


def test_every_setting_is_passed_or_explicitly_not_passed(
    compose: dict[str, Any],
) -> None:
    fields = {name.upper() for name in SettingsSchema.model_fields}
    for service in ("api", "worker"):
        passed = set(_environment(compose, service))
        skipped = WORKER_NOT_PASSED if service == "worker" else NOT_PASSED
        missing = fields - passed - skipped
        assert not missing, f"{service} does not receive {sorted(missing)}"
    assert fields >= NOT_PASSED


def test_optional_variables_have_defaults_and_required_are_guarded(
    compose: dict[str, Any],
) -> None:
    text = COMPOSE.read_text()
    unguarded_text = text.replace("$$", "")
    for variable in REQUIRED:
        assert f"${{{variable}:?" in text, variable
        assert f"${{{variable}}}" not in unguarded_text, variable
    for name, value in _environment(compose, "worker").items():
        for variable, modifier in re.findall(r"\$\{(\w+)([^}]*)\}", value):
            expected = ":?" if variable in REQUIRED else ":-"
            assert modifier.startswith(expected), f"{name}: {variable}"


@pytest.mark.parametrize(
    ("service", "volume", "target"),
    [
        ("postgres", "postgres-data", "/var/lib/postgresql/data"),
        ("rabbitmq", "rabbitmq-data", "/var/lib/rabbitmq"),
    ],
)
def test_data_lives_in_a_named_volume(
    compose: dict[str, Any], service: str, volume: str, target: str
) -> None:
    assert f"{volume}:{target}" in compose["services"][service]["volumes"]
    assert volume in compose["volumes"]


def test_worker_has_a_healthcheck_on_the_metrics_port(
    compose: dict[str, Any],
) -> None:
    check = " ".join(compose["services"]["worker"]["healthcheck"]["test"])
    assert "/metrics" in check
    assert "WORKER_METRICS_PORT" in check


def test_dockerfile_poetry_version_matches_ci() -> None:
    root = COMPOSE.parent
    dockerfile = (root / "Dockerfile").read_text()
    ci = (root / ".github" / "workflows" / "ci.yml").read_text()
    docker_version = re.search(r"^ARG POETRY_VERSION=(\S+)$", dockerfile, re.M)
    ci_version = re.search(r'^\s*POETRY_VERSION: "([^"]+)"', ci, re.M)
    assert docker_version and ci_version
    assert docker_version.group(1) == ci_version.group(1)
    assert re.search(r"^FROM python:3\.12\.\d+-slim$", dockerfile, re.M)
