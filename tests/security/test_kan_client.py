"""The Kan helper script only talks http(s)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "kan.py"


def _kan_client() -> type:
    spec = importlib.util.spec_from_file_location("kan_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.KanClient  # type: ignore[no-any-return]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://h/x", "h/api"])
def test_non_http_scheme_is_rejected(url: str) -> None:
    with pytest.raises(SystemExit, match="http"):
        _kan_client()(url, "key", "board")


@pytest.mark.parametrize("url", ["http://localhost:3000/api/v1", "https://h"])
def test_http_schemes_are_accepted(url: str) -> None:
    assert _kan_client()(url, "key", "board").url == url
