from functools import lru_cache
from typing import Final

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder shipped in .env.example; must never be accepted as a real key.
JWT_SECRET_PLACEHOLDER: Final = "change-me-generate-a-random-secret-key"
JWT_SECRET_MIN_LENGTH: Final = 32
ADMIN_SECRET_MIN_LENGTH: Final = 32


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

    # Admin panel (fastadmin). Blank means unset and the panel is not
    # mounted. Signs the panel's own session cookie, separate from the JWTs.
    admin_secret_key: SecretStr | None = None
    # Set false only for local plain-HTTP development.
    admin_session_cookie_secure: bool = True
    # Lifetime of a panel session. Signing out only clears the browser
    # cookie; the signed value itself stays valid until this expires.
    admin_session_ttl_seconds: int = Field(default=28800, ge=60)

    # Serves Swagger UI, ReDoc and the OpenAPI schema when true.
    api_docs_enabled: bool = True

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

    @field_validator("admin_secret_key", mode="before")
    @classmethod
    def _blank_admin_secret_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("admin_secret_key")
    @classmethod
    def _admin_secret_is_strong(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        # Messages name the setting but never echo the value.
        if value is not None and (
            len(value.get_secret_value()) < ADMIN_SECRET_MIN_LENGTH
        ):
            raise ValueError(
                f"admin_secret_key must be at least {ADMIN_SECRET_MIN_LENGTH} "
                "characters"
            )
        return value

    @model_validator(mode="after")
    def _admin_secret_differs_from_jwt(self) -> "SettingsSchema":
        if (
            self.admin_secret_key is not None
            and self.admin_secret_key.get_secret_value()
            == self.jwt_secret_key.get_secret_value()
        ):
            raise ValueError("admin_secret_key must differ from jwt_secret_key")
        return self

    @field_validator("pricing_file")
    @classmethod
    def _blank_pricing_file_is_unset(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()


@lru_cache
def get_settings() -> SettingsSchema:
    return SettingsSchema()
