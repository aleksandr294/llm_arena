from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # Max simultaneous model requests per provider in one worker process.
    llm_max_concurrency: int = Field(default=4, ge=1)

    # Optional operator-supplied pricing file used to estimate costs. Blank
    # means unset; no prices are shipped.
    pricing_file: str | None = None

    @field_validator("pricing_file")
    @classmethod
    def _blank_pricing_file_is_unset(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()


@lru_cache
def get_settings() -> SettingsSchema:
    return SettingsSchema()
