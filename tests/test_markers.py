"""The pytest configuration rejects unknown markers."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_pytest(
    tmp_path: Path, source: str
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_sample.py").write_text(source)
    # The repository's own configuration, but not its conftest.py: the
    # child runs in ``tmp_path``.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "--rootdir",
            str(tmp_path),
            "-p",
            "no:cacheprovider",
            str(tmp_path / "test_sample.py"),
        ],
        cwd=tmp_path,
        # A developer's own pytest options must not change the outcome.
        env={
            k: v
            for k, v in os.environ.items()
            if k not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_misspelled_marker_fails_collection(tmp_path: Path) -> None:
    result = _run_pytest(
        tmp_path,
        "import pytest\n\n\n"
        "@pytest.mark.integraton\n"
        "def test_sample() -> None:\n"
        "    pass\n",
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "'integraton' not found in `markers` configuration" in output


def test_a_registered_marker_is_accepted(tmp_path: Path) -> None:
    result = _run_pytest(
        tmp_path,
        "import pytest\n\n\n"
        "@pytest.mark.integration\n"
        "def test_sample() -> None:\n"
        "    pass\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
