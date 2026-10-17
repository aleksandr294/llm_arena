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


def test_provider_keys_optional_and_masked(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    unset = SettingsSchema(_env_file=None)
    assert unset.openai_api_key is None
    assert unset.anthropic_api_key is None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic-secret")
    settings = SettingsSchema(_env_file=None)

    rendered = repr(settings) + str(settings) + settings.model_dump_json()
    assert "sk-test" not in rendered
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-openai-secret"


def test_llm_max_concurrency_default_and_minimum(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)
    monkeypatch.delenv("LLM_MAX_CONCURRENCY", raising=False)
    assert SettingsSchema(_env_file=None).llm_max_concurrency == 4
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        SettingsSchema(_env_file=None)


def test_pricing_file_optional_and_blank_is_unset(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)
    monkeypatch.delenv("PRICING_FILE", raising=False)
    assert SettingsSchema(_env_file=None).pricing_file is None
    monkeypatch.setenv("PRICING_FILE", "  ")
    assert SettingsSchema(_env_file=None).pricing_file is None
    monkeypatch.setenv("PRICING_FILE", "docs/pricing.example.json")
    assert (
        SettingsSchema(_env_file=None).pricing_file
        == "docs/pricing.example.json"
    )


def _base_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)


def test_jwt_secret_is_required(monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError) as exc:
        SettingsSchema(_env_file=None)

    assert "jwt_secret_key" in str(exc.value)


@pytest.mark.parametrize("secret", ["", "short-secret-value", "x" * 31])
def test_jwt_secret_too_short_is_rejected_without_echo(
    monkeypatch: MonkeyPatch, secret: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", secret)

    with pytest.raises(ValidationError) as exc:
        SettingsSchema(_env_file=None)

    message = str(exc.value)
    assert "jwt_secret_key" in message
    if secret:
        assert secret not in message


def test_jwt_secret_placeholder_from_env_example_is_rejected(
    monkeypatch: MonkeyPatch,
) -> None:
    placeholder = ""
    with open(".env.example") as f:
        for line in f:
            if line.startswith("JWT_SECRET_KEY="):
                placeholder = line.split("=", 1)[1].strip()
    assert placeholder
    _base_env(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", placeholder)

    with pytest.raises(ValidationError) as exc:
        SettingsSchema(_env_file=None)

    assert "jwt_secret_key" in str(exc.value)
    assert placeholder not in str(exc.value)


def test_jwt_settings_defaults_and_masking(monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch)
    secret = "a-valid-secret-with-at-least-32-characters"
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    for name in (
        "JWT_ACCESS_TTL_SECONDS",
        "JWT_REFRESH_TTL_SECONDS",
        "JWT_ISSUER",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = SettingsSchema(_env_file=None)

    assert settings.jwt_access_ttl_seconds == 900
    assert settings.jwt_refresh_ttl_seconds == 604800
    assert settings.jwt_issuer == "llm-arena"
    rendered = repr(settings) + str(settings) + settings.model_dump_json()
    assert secret not in rendered
