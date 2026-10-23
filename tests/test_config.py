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


def test_max_stored_response_chars_default_and_minimum(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)
    monkeypatch.delenv("MAX_STORED_RESPONSE_CHARS", raising=False)
    settings = SettingsSchema(_env_file=None)
    assert settings.max_stored_response_chars == 1_000_000
    monkeypatch.setenv("MAX_STORED_RESPONSE_CHARS", "500")
    assert SettingsSchema(_env_file=None).max_stored_response_chars == 500
    for bad in ("0", "-1", "abc"):
        monkeypatch.setenv("MAX_STORED_RESPONSE_CHARS", bad)
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


ADMIN_SECRET = "an-admin-secret-with-at-least-32-characters"


def test_api_host_defaults_to_loopback(monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("API_HOST", raising=False)

    assert SettingsSchema(_env_file=None).api_host == "127.0.0.1"

    monkeypatch.setenv("API_HOST", "0.0.0.0")
    assert SettingsSchema(_env_file=None).api_host == "0.0.0.0"


def test_admin_settings_defaults(monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch)
    for name in (
        "ADMIN_SECRET_KEY",
        "ADMIN_SESSION_COOKIE_SECURE",
        "API_DOCS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = SettingsSchema(_env_file=None)

    assert settings.admin_secret_key is None
    assert settings.admin_session_cookie_secure is True
    assert settings.admin_session_ttl_seconds == 28800
    assert settings.api_docs_enabled is True


@pytest.mark.parametrize("ttl", ["0", "59", "-1"])
def test_admin_session_ttl_below_minimum_rejected(
    monkeypatch: MonkeyPatch, ttl: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_SESSION_TTL_SECONDS", ttl)

    with pytest.raises(ValidationError):
        SettingsSchema(_env_file=None)


def test_admin_session_ttl_is_configurable(monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_SESSION_TTL_SECONDS", "3600")

    assert SettingsSchema(_env_file=None).admin_session_ttl_seconds == 3600


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_admin_secret_is_unset(
    monkeypatch: MonkeyPatch, blank: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_SECRET_KEY", blank)

    assert SettingsSchema(_env_file=None).admin_secret_key is None


def test_admin_secret_valid_and_masked(monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_SECRET_KEY", ADMIN_SECRET)

    settings = SettingsSchema(_env_file=None)

    assert settings.admin_secret_key is not None
    assert settings.admin_secret_key.get_secret_value() == ADMIN_SECRET
    rendered = repr(settings) + str(settings) + settings.model_dump_json()
    assert ADMIN_SECRET not in rendered


@pytest.mark.parametrize("secret", ["short-admin-secret", "x" * 31])
def test_weak_admin_secret_rejected_without_echo(
    monkeypatch: MonkeyPatch, secret: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_SECRET_KEY", secret)

    with pytest.raises(ValidationError) as exc:
        SettingsSchema(_env_file=None)

    message = str(exc.value)
    assert "admin_secret_key" in message
    assert secret not in message


def test_admin_secret_equal_to_jwt_secret_rejected_without_echo(
    monkeypatch: MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    shared = "a-shared-secret-value-with-32-plus-characters"
    monkeypatch.setenv("JWT_SECRET_KEY", shared)
    monkeypatch.setenv("ADMIN_SECRET_KEY", shared)

    with pytest.raises(ValidationError) as exc:
        SettingsSchema(_env_file=None)

    message = str(exc.value)
    assert "admin_secret_key" in message
    assert shared not in message


def test_admin_cookie_and_docs_flags_parse(monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("ADMIN_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("API_DOCS_ENABLED", "false")

    settings = SettingsSchema(_env_file=None)

    assert settings.admin_session_cookie_secure is False
    assert settings.api_docs_enabled is False
