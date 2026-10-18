"""Every pointer in the OWASP section of ``docs/security.md`` must resolve.

Parsing is deliberately simple. Only the text between the
``## OWASP coverage`` heading and the next ``## `` heading is read. Each
backtick-quoted span is classified:

* ``path::test_name``: the file must exist and contain ``def test_name``
  (sync or async);
* a file-looking span (a known extension, or ``Dockerfile``): the path
  relative to the repository root must exist;
* anything else (header names, settings, URL patterns) is ignored.

Every OWASP item must also have a valid status and at least one pointer.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "security.md"
SECTION_HEADING = "## OWASP coverage"
STATUSES = {"covered", "partially covered", "gap", "not applicable"}
FILE_SPAN = re.compile(r"^[\w./-]+\.(py|md|toml|yml|yaml|json|example)$")
SPAN = re.compile(r"`([^`]+)`")
EXPECTED_ITEMS = [f"A{n:02d}" for n in range(1, 11)] + [
    f"API{n}" for n in range(1, 11)
]


def _section() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(SECTION_HEADING)
    end = text.find("\n## ", start + len(SECTION_HEADING))
    return text[start : end if end != -1 else len(text)]


def _rows() -> dict[str, list[str]]:
    """Table rows keyed by the item code (A01, API1, ...)."""
    rows: dict[str, list[str]] = {}
    for line in _section().splitlines():
        match = re.match(r"^\| (A\d{2}|API\d{1,2}) ", line)
        if match:
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            rows[match.group(1)] = cells
    return rows


def _is_pointer(span: str) -> bool:
    path = span.split("::", 1)[0]
    return bool(FILE_SPAN.match(path)) or path == "Dockerfile"


def _spans(text: str) -> list[str]:
    return [s for s in SPAN.findall(text) if _is_pointer(s)]


def test_every_item_has_a_row() -> None:
    assert sorted(_rows()) == sorted(EXPECTED_ITEMS)


def test_every_row_has_a_valid_status_and_a_pointer() -> None:
    for item, cells in _rows().items():
        assert len(cells) == 4, item
        assert cells[1] in STATUSES, item
        assert _spans(cells[3]), f"{item} has no evidence pointer"


def test_every_cited_path_and_test_exists() -> None:
    spans = _spans(_section())
    assert spans
    missing: list[str] = []
    for span in spans:
        path, _, name = span.partition("::")
        file = ROOT / path
        if not file.is_file() or (
            name
            and not re.search(
                rf"^\s*(async )?def {re.escape(name)}\(",
                file.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        ):
            missing.append(span)
    assert not missing, f"unresolved pointers: {missing}"
