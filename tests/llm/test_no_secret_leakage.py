"""Keys, prompts and responses must not reach logs or error messages."""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx2
import openai
import pytest

from components.core.config import SettingsSchema
from components.third_party.llm.providers import anthropic as anthropic_mod
from components.third_party.llm.providers import openai as openai_mod
from components.third_party.llm.providers.base import LLMProviderError
from components.third_party.llm.schemas import ModelTargetSchema, ProviderName
from components.third_party.llm.service import (
    LLMService,
    ProviderNotConfiguredError,
)

KEY = "sk-SENTINEL-KEY-123"
PROMPT = "SENTINEL-PROMPT-456"
SYSTEM = "SENTINEL-SYSTEM-789"
RESPONSE = "SENTINEL-RESPONSE-012"
SENTINELS = (KEY, PROMPT, SYSTEM, RESPONSE)

HTTP_REQUEST = httpx2.Request("POST", "https://example.invalid/v1")


def _settings(**overrides: Any) -> SettingsSchema:
    values: dict[str, Any] = {
        "database_url": "postgresql://x",
        "rabbitmq_url": "amqp://x",
        "openai_api_key": KEY,
        "anthropic_api_key": KEY,
        **overrides,
    }
    return SettingsSchema(_env_file=None, **values)


def _install_clients(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None
) -> None:
    openai_completion = SimpleNamespace(
        model="m",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=RESPONSE),
                finish_reason="stop",
            )
        ],
        usage=None,
        _request_id="req_1",
    )
    openai_client = MagicMock()
    openai_client.chat.completions.create = AsyncMock(
        return_value=openai_completion, side_effect=error
    )
    anthropic_message = SimpleNamespace(
        model="m",
        content=[SimpleNamespace(type="text", text=RESPONSE)],
        stop_reason="end_turn",
        usage=None,
        _request_id="req_1",
    )
    anthropic_client = MagicMock()
    anthropic_client.messages.create = AsyncMock(
        return_value=anthropic_message, side_effect=error
    )
    monkeypatch.setattr(openai_mod, "AsyncOpenAI", lambda **_: openai_client)
    monkeypatch.setattr(
        anthropic_mod, "AsyncAnthropic", lambda **_: anthropic_client
    )


def _assert_clean(text: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in text


def _status_error(cls: Any) -> Exception:
    response = httpx2.Response(500, request=HTTP_REQUEST)
    # The SDK message deliberately echoes sensitive data.
    error: Exception = cls(
        f"{KEY} {PROMPT} {RESPONSE}", response=response, body=None
    )
    return error


@pytest.mark.parametrize("provider", list(ProviderName))
async def test_success_path_does_not_log_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    provider: ProviderName,
) -> None:
    _install_clients(monkeypatch, None)
    caplog.set_level(logging.DEBUG)
    result = await LLMService(_settings()).generate(
        ModelTargetSchema(provider=provider, model="m"),
        PROMPT,
        system_prompt=SYSTEM,
    )
    assert result.text == RESPONSE  # the caller still gets the content
    assert caplog.records
    _assert_clean(caplog.text)


@pytest.mark.parametrize(
    ("provider", "error"),
    [
        (ProviderName.OPENAI, _status_error(openai.InternalServerError)),
        (
            ProviderName.OPENAI,
            openai.APITimeoutError(request=HTTP_REQUEST),
        ),
        (ProviderName.OPENAI, ValueError(f"{KEY} {PROMPT}")),
        (
            ProviderName.ANTHROPIC,
            _status_error(anthropic.InternalServerError),
        ),
        (
            ProviderName.ANTHROPIC,
            anthropic.APITimeoutError(request=HTTP_REQUEST),
        ),
        (ProviderName.ANTHROPIC, ValueError(f"{KEY} {PROMPT}")),
    ],
)
async def test_failure_path_does_not_leak_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    provider: ProviderName,
    error: Exception,
) -> None:
    _install_clients(monkeypatch, error)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(LLMProviderError) as excinfo:
        await LLMService(_settings()).generate(
            ModelTargetSchema(provider=provider, model="m"),
            PROMPT,
            system_prompt=SYSTEM,
        )
    _assert_clean(str(excinfo.value))
    _assert_clean(repr(excinfo.value))
    _assert_clean(caplog.text)


async def test_not_configured_error_has_no_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_clients(monkeypatch, None)
    caplog.set_level(logging.DEBUG)
    service = LLMService(_settings(openai_api_key=None))
    with pytest.raises(ProviderNotConfiguredError) as excinfo:
        await service.generate(
            ModelTargetSchema(provider=ProviderName.OPENAI, model="m"),
            PROMPT,
        )
    _assert_clean(str(excinfo.value))
    _assert_clean(caplog.text)
