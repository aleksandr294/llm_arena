"""Tests must build ``SettingsSchema`` without reading a ``.env`` file.

A developer's ``.env`` would otherwise change test results. Use
``tests.helpers.make_settings``, or pass ``_env_file=None`` when a test needs
to control the environment itself (``tests/test_config.py``).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASS_NAME = "SettingsSchema"


def _is_settings_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == CLASS_NAME
    return isinstance(func, ast.Attribute) and func.attr == CLASS_NAME


def find_env_file_calls(source: str) -> list[int]:
    """1-based lines of ``SettingsSchema(...)`` calls without ``_env_file``."""
    bad: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and _is_settings_call(node)
            and not any(kw.arg == "_env_file" for kw in node.keywords)
        ):
            bad.append(node.lineno)
    return sorted(bad)


def test_detector_flags_a_call_without_env_file() -> None:
    source = (
        "SettingsSchema(database_url='x')\n"
        "SettingsSchema(_env_file=None)\n"
        "config.SettingsSchema(**values)\n"
    )

    assert find_env_file_calls(source) == [1, 3]


def test_no_test_reads_the_env_file() -> None:
    files = [ROOT / "conftest.py", *sorted((ROOT / "tests").rglob("*.py"))]
    offenders = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in files
        for line in find_env_file_calls(path.read_text())
    ]

    assert not offenders, (
        "SettingsSchema built without _env_file=None (use make_settings): "
        + ", ".join(offenders)
    )
