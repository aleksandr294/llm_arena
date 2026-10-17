from functools import lru_cache
from typing import Final

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder shipped in .env.example; must never be accepted as a real key.
JWT_SECRET_PLACEHOLDER: Final = "change-me-generate-a-random-secret-key"
JWT_SECRET_MIN_LENGTH: Final = 32


class SettingsSchema(BaseSettings):
    """Environment-based settings. Secrets are ``SecretStr`` so they are
    masked in ``repr`` and serialized output."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    health_check_timeout_seconds: float = 2.0

    database_url: SecretStr
    rabbitmq_url: SecretStr

    # Signs JWTs (HS256). Required; rotating it invalidates all sessions.
    jwt_secret_key: SecretStr
    jwt_access_ttl_seconds: int = Field(default=900, ge=1)
    jwt_refresh_ttl_seconds: int = Field(default=604800, ge=1)
    jwt_issuer: str = Field(default="llm-arena", min_length=1)

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # Max simultaneous model requests per provider in one worker process.
    llm_max_concurrency: int = Field(default=4, ge=1)

    # Optional operator-supplied pricing file used to estimate costs. Blank
    # means unset; no prices are shipped.
    pricing_file: str | None = None

    @field_validator("jwt_secret_key")
    @classmethod
    def _jwt_secret_is_strong(cls, value: SecretStr) -> SecretStr:
        # Messages name the setting but never echo the value.
        secret = value.get_secret_value()
        if len(secret) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(
                f"jwt_secret_key must be at least {JWT_SECRET_MIN_LENGTH} "
                "characters"
            )
        if secret == JWT_SECRET_PLACEHOLDER:
            raise ValueError(
                "jwt_secret_key must not be the .env.example placeholder"
            )
        return value

    @field_validator("pricing_file")
    @classmethod
    def _blank_pricing_file_is_unset(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()


@lru_cache
def get_settings() -> SettingsSchema:
    return SettingsSchema()
