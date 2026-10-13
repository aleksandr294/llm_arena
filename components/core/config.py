from functools import lru_cache

from pydantic import SecretStr
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


@lru_cache
def get_settings() -> SettingsSchema:
    return SettingsSchema()
