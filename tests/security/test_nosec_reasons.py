"""Every production Ruff security suppression must state its reason.

A suppression of a security rule is ``# noqa: S105 - reason``. Only ``S``
codes are policed here; ``noqa`` comments for other rules are left alone.
"""

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
# A noqa comment with a code list, of which at least one is an S rule.
NOQA = re.compile(
    r"#\s*noqa\s*:\s*(?P<codes>[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)(?P<rest>.*)$",
    re.IGNORECASE,
)
# What must follow the codes: "- reason" with some text.
REASON = re.compile(r"^\s+-\s+\S")


def _has_security_code(codes: str) -> bool:
    return any(
        code.strip().upper().startswith("S") and code.strip()[1:2].isdigit()
        for code in codes.split(",")
    )


def find_unjustified_noqa(source: str) -> list[int]:
    """Return 1-based line numbers of ``S`` suppressions lacking a reason."""
    bad: list[int] = []
    for number, line in enumerate(source.splitlines(), start=1):
        match = NOQA.search(line)
        if (
            match is not None
            and _has_security_code(match.group("codes"))
            and not REASON.match(match.group("rest"))
        ):
            bad.append(number)
    return bad


def find_bare_noqa(source: str) -> list[int]:
    """Return line numbers of blanket ``# noqa`` without any rule code."""
    return [
        number
        for number, line in enumerate(source.splitlines(), start=1)
        if re.search(r"#\s*noqa\s*(?:$|#)", line, re.IGNORECASE)
    ]


def _production_files() -> list[Path]:
    files: list[Path] = []
    for name in PRODUCTION_PATHS:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.py"))
    return files


def test_production_security_suppressions_have_reasons() -> None:
    violations = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in _production_files()
        for number in find_unjustified_noqa(path.read_text("utf-8"))
    ]
    assert violations == []


def test_production_has_no_blanket_noqa() -> None:
    violations = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in _production_files()
        for number in find_bare_noqa(path.read_text("utf-8"))
    ]
    assert violations == []


def test_checker_flags_missing_reason() -> None:
    source = "\n".join(
        [
            "a = 1  # noqa: S311",
            "b = 2  # noqa: S311 -",
            "c = 3  # noqa: S105, S311",
            "d = 4  # noqa: S311 - non-cryptographic jitter",
            "e = 5  # noqa: F401",
            "f = 6  # noqa: E501, S105 - placeholder, not a password",
            "g = 7",
            "h = 8  # noqa: S311-no space before reason",
        ]
    )
    assert find_unjustified_noqa(source) == [1, 2, 3, 8]


def test_checker_ignores_other_rules_without_reason() -> None:
    source = "import x  # noqa: F401\ny = 1  # noqa: E501\n"
    assert find_unjustified_noqa(source) == []


def test_bare_noqa_is_detected() -> None:
    source = "a = 1  # noqa\nb = 2  # noqa: S311 - reason\nc = 3  # NOQA\n"
    assert find_bare_noqa(source) == [1, 3]
