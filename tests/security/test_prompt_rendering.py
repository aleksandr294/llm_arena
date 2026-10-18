"""Prompts are built by literal, single-pass substitution of the two
placeholders; the render helpers do no I/O."""

import ast
from pathlib import Path

import pytest

from components.experiments import utils as experiment_utils
from components.experiments.utils import (
    EXPECTED_OUTPUT_TOKEN,
    INPUT_TOKEN,
    render_prompt,
)
from tests.security.helpers import CORPUS, SAMPLE_IDS, SAMPLES, TEMPLATE_SAFE

TEMPLATE = f"Q: {INPUT_TOKEN} | A: {EXPECTED_OUTPUT_TOKEN} | end"


def _reference(template: str, input_text: str, expected: str) -> str:
    """Independent left-to-right scan that never re-reads inserted text."""
    out: list[str] = []
    i = 0
    while i < len(template):
        if template.startswith(INPUT_TOKEN, i):
            out.append(input_text)
            i += len(INPUT_TOKEN)
        elif template.startswith(EXPECTED_OUTPUT_TOKEN, i):
            out.append(expected)
            i += len(EXPECTED_OUTPUT_TOKEN)
        else:
            out.append(template[i])
            i += 1
    return "".join(out)


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_hostile_input_is_inserted_unchanged(sample: str) -> None:
    prompt = render_prompt(TEMPLATE, sample, "EXPECTED")
    assert prompt == _reference(TEMPLATE, sample, "EXPECTED")
    # The inserted text is a contiguous, unmodified slice.
    assert f"Q: {sample} | A: EXPECTED | end" == prompt


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_hostile_expected_output_is_inserted_unchanged(sample: str) -> None:
    prompt = render_prompt(TEMPLATE, "IN", sample)
    assert prompt == f"Q: IN | A: {sample} | end"


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_hostile_both_values_are_never_rescanned(sample: str) -> None:
    prompt = render_prompt(TEMPLATE, sample, sample)
    assert prompt == f"Q: {sample} | A: {sample} | end"


@pytest.mark.parametrize("name", list(TEMPLATE_SAFE))
def test_hostile_template_text_is_literal(name: str) -> None:
    template = f"{TEMPLATE_SAFE[name]} {INPUT_TOKEN}"
    assert render_prompt(template, "IN", None) == f"{TEMPLATE_SAFE[name]} IN"


@pytest.mark.parametrize(
    "literal",
    [
        "{input}",
        "{expected_output}",
        "{0}",
        "{{x}}",
        "${x}",
        "%s",
        "{__class__}",
    ],
)
def test_spec_listed_syntax_in_a_case_is_unchanged(literal: str) -> None:
    assert (
        render_prompt(TEMPLATE, literal, "EXPECTED")
        == f"Q: {literal} | A: EXPECTED | end"
    )


def test_placeholder_smuggling_is_not_substituted() -> None:
    # Input holding the other placeholder keeps it; expected output holding
    # the input placeholder keeps it too.
    assert (
        render_prompt(TEMPLATE, EXPECTED_OUTPUT_TOKEN, "SECRET")
        == f"Q: {EXPECTED_OUTPUT_TOKEN} | A: SECRET | end"
    )
    assert (
        render_prompt(TEMPLATE, "IN", INPUT_TOKEN)
        == f"Q: IN | A: {INPUT_TOKEN} | end"
    )
    assert "SECRET" not in render_prompt(
        INPUT_TOKEN, EXPECTED_OUTPUT_TOKEN, "SECRET"
    )


def test_missing_expected_output_renders_empty_not_none() -> None:
    assert render_prompt(TEMPLATE, "IN", None) == "Q: IN | A:  | end"


def test_large_values_render_in_linear_time() -> None:
    big = "x" * 5_000_000
    prompt = render_prompt(TEMPLATE, big, big)
    assert prompt == f"Q: {big} | A: {big} | end"


def test_corpus_covers_the_required_families() -> None:
    joined = "\n".join(CORPUS)
    for family in (
        "template",
        "smuggle",
        "attack",
        "code",
        "formula",
        "control",
    ):
        assert family in joined


FORBIDDEN_IMPORT_PARTS = (
    "repository",
    "models",
    "sqlalchemy",
    "database",
    "httpx",
    "requests",
    "socket",
    "subprocess",
    "os",
    "pathlib",
    "shutil",
    "urllib",
    "asyncio",
    "logging",
    "random",
    "time",
)
FORBIDDEN_CALLS = {
    "open",
    "print",
    "input",
    "eval",
    "exec",
    "__import__",
    "getattr",
    "setattr",
    "globals",
    "locals",
    "system",
    "popen",
    "run",
    "connect",
    "execute",
    "commit",
    "flush",
    "read_text",
    "write_text",
    "format",
    "format_map",
}


def _tree() -> ast.Module:
    return ast.parse(Path(experiment_utils.__file__).read_text())


def _imported_modules(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def test_render_helpers_import_no_repository_or_io_module() -> None:
    for module in _imported_modules(_tree()):
        parts = module.split(".")
        assert not any(p in FORBIDDEN_IMPORT_PARTS for p in parts), module
    # Whole-module allow list: the helpers are pure on purpose.
    allowed = {
        "re",
        "collections.abc",
        "components.experiments.enums",
    }
    assert set(_imported_modules(_tree())) <= allowed


def test_render_helpers_make_no_io_or_dynamic_calls() -> None:
    called: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
                # The builtin; ``re.compile`` (an attribute) is fine.
                assert func.id != "compile"
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    assert not called & FORBIDDEN_CALLS, called & FORBIDDEN_CALLS


def test_render_helpers_are_not_async_and_take_no_session() -> None:
    tree = _tree()
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            names = {a.arg for a in node.args.args}
            assert not names & {"session", "scope", "repository"}, node.name
