"""Mapper configuration must not depend on import order."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNIPPET = """
import {module}
from sqlalchemy.orm import configure_mappers
from components.core.database import Base
configure_mappers()
assert "organizations" in Base.metadata.tables
assert "users" in Base.metadata.tables
"""


@pytest.mark.parametrize(
    "module", ["components.users.models", "components.auth.models"]
)
def test_models_import_alone_configures_mappers(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", SNIPPET.format(module=module)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT), "JWT_SECRET_KEY": "x" * 40},
    )
    assert result.returncode == 0, result.stderr
