"""Static check of the revision graph; needs no database."""

from pathlib import Path

from alembic.script import ScriptDirectory

from tests.migrations.helpers import alembic_config


def test_there_is_exactly_one_head() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    heads = script.get_heads()
    assert (
        len(heads) == 1
    ), "two migrations share a parent; heads: " + ", ".join(sorted(heads))


def test_every_revision_file_is_in_the_graph() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    files = list(Path(script.versions).glob("*.py"))
    assert len(list(script.walk_revisions())) == len(files)
