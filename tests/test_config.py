import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import ValidationError

from components.core.config import SettingsSchema

DB_URL = "postgresql+asyncpg://u:s3cret-pw@localhost/db"
MQ_URL = "amqp://u:s3cret-mq@localhost/"


def test_missing_required_setting_names_it(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)

    with pytest.raises(ValidationError) as exc:
        SettingsSchema(_env_file=None)

    assert "database_url" in str(exc.value)


def test_secrets_are_masked(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)

    settings = SettingsSchema(_env_file=None)

    rendered = repr(settings) + str(settings) + settings.model_dump_json()
    assert "s3cret" not in rendered
    assert settings.database_url.get_secret_value() == DB_URL


def test_env_example_lists_every_setting() -> None:
    names = {n.upper() for n in SettingsSchema.model_fields}
    with open(".env.example") as f:
        text = f.read()
    for name in names:
        assert f"{name}=" in text


def test_validation_error_does_not_leak_secrets(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)

    with pytest.raises(ValidationError) as exc:
        SettingsSchema(_env_file=None)

    assert "s3cret" not in str(exc.value)
