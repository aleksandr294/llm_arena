"""Every production Bandit suppression must state its reason."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_PATHS = (
    "components",
    "api",
    "admin",
    "cli.py",
    "main.py",
    "taskiq_app.py",
    "migrations",
    "scripts",
)
# "# nosec", optional test ids, then "- reason" with some text.
NOSEC = re.compile(r"#\s*nosec\b(?P<rest>.*)$")
REASON = re.compile(r"^\s*(?:B\d+(?:\s*,\s*B\d+)*)?\s+-\s+\S")


def find_unjustified_nosec(source: str) -> list[int]:
    """Return 1-based line numbers of ``# nosec`` comments lacking a reason."""
    bad: list[int] = []
    for number, line in enumerate(source.splitlines(), start=1):
        match = NOSEC.search(line)
        if match is not None and not REASON.match(match.group("rest")):
            bad.append(number)
    return bad


def _production_files() -> list[Path]:
    files: list[Path] = []
    for name in PRODUCTION_PATHS:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.py"))
    return files


def test_production_nosec_comments_have_reasons() -> None:
    violations = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in _production_files()
        for number in find_unjustified_nosec(path.read_text("utf-8"))
    ]
    assert violations == []


def test_checker_flags_missing_reason() -> None:
    source = "\n".join(
        [
            "a = 1  # nosec",
            "b = 2  # nosec B311",
            "c = 3  # nosec B311 -",
            "d = 4  # nosec B311 - non-cryptographic jitter",
            "e = 5  # nosec - reason without id",
            "f = 6",
        ]
    )
    assert find_unjustified_nosec(source) == [1, 2, 3]
