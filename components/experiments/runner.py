"""Executes one experiment result.

All state lives in database rows. A result is run as three atomic
operations, each in its own ``scope()`` session and transaction: the claim
(committed before the model call so concurrent claimers see it), the model
call with no session and no transaction open, and the conditional write of
the outcome together with the experiment finalization. Logs carry ids,
status, attempt and error kind only, never prompts, responses, keys or
provider messages.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from components.core.config import DEFAULT_MAX_STORED_RESPONSE_CHARS
from components.core.database import SessionScope
from components.experiments.enums import ResultStatus
from components.experiments.models import Experiment
from components.experiments.repository import (
    LEASE_SECONDS,
    ExperimentRepository,
    ExperimentResultRepository,
    finalize_experiment,
)
from components.experiments.schemas import ExperimentConfigSchema
from components.experiments.utils import backoff_delay
from components.third_party.llm.enums import ProviderName
from components.third_party.llm.exceptions import (
    LLMProviderError,
    ProviderNotConfiguredError,
    UnsupportedProviderError,
)
from components.third_party.llm.schemas import (
    LLMResponseSchema,
    ModelTargetSchema,
)
from components.third_party.llm.service import LLMService

logger = logging.getLogger(__name__)

KIND_CONFIGURATION = "configuration"
KIND_INTERNAL = "internal"
JITTER_MAX_SECONDS = 0.5
HOOK_TIMEOUT_SECONDS = 10.0
# Column sizes of experiment_results; provider values are untrusted.
FINISH_REASON_MAX = 64
IDENTIFIER_MAX = 255
# Appended to a stored response that was cut at the size limit.
TRUNCATION_MARKER = "\n[... response truncated ...]"

Sleep = Callable[[float], Awaitable[None]]
# Called with the result id after a succeeded outcome was stored.
OnSucceeded = Callable[[uuid.UUID], Awaitable[None]]


@dataclass(frozen=True)
class _Work:
    """Plain snapshot of what a model call needs, so no ORM object is used
    while no session is open."""

    experiment_id: uuid.UUID
    target: ModelTargetSchema
    prompt: str
    config: ExperimentConfigSchema
    attempts: int
    claimed_started_at: datetime


@dataclass(frozen=True)
class _Outcome:
    status: ResultStatus
    fields: dict[str, Any]


def _clean(value: str | None, max_length: int) -> str | None:
    """Drop NUL bytes (rejected by PostgreSQL) and fit the column."""
    if value is None:
        return None
    return value.replace("\x00", "")[:max_length]


def _default_jitter() -> float:
    return random.uniform(  # nosec B311 - non-cryptographic retry jitter
        0, JITTER_MAX_SECONDS
    )


def _bound_response(text: str, limit: int) -> tuple[str, bool]:
    """Strip NUL bytes and fit ``limit`` characters, marker included.
    Returns the text and whether it was cut; text within the limit is
    returned unchanged."""
    text = text.replace("\x00", "")
    if len(text) <= limit:
        return text, False
    keep = max(limit - len(TRUNCATION_MARKER), 0)
    return (text[:keep] + TRUNCATION_MARKER)[:limit], True


class ExperimentRunner:
    def __init__(
        self,
        scope: SessionScope,
        llm_service: LLMService,
        max_concurrency: int = 4,
        *,
        lease_seconds: int = LEASE_SECONDS,
        sleep: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = _default_jitter,
        on_succeeded: OnSucceeded | None = None,
        max_response_chars: int = DEFAULT_MAX_STORED_RESPONSE_CHARS,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if max_response_chars < 1:
            raise ValueError("max_response_chars must be at least 1")
        self._max_response_chars = max_response_chars
        self._scope = scope
        self._llm = llm_service
        self._max_concurrency = max_concurrency
        self._lease_seconds = lease_seconds
        self._sleep = sleep
        self._jitter = jitter
        self._on_succeeded = on_succeeded
        # Process-local, one per provider; bounds requests, not backoff.
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _semaphore(self, provider: str) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(provider)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._max_concurrency)
            self._semaphores[provider] = semaphore
        return semaphore

    async def run(self, result_id: uuid.UUID) -> None:
        """Execute one result; a no-op when it is owned or finished."""
        work = await self._claim(result_id)
        if work is None:
            logger.info("result %s not claimable; skipping", result_id)
            return
        outcome = await self._execute(work)
        await self._persist_safely(result_id, work, outcome)

    async def _claim(self, result_id: uuid.UUID) -> _Work | None:
        async with self._scope() as session:
            claimed = await ExperimentResultRepository(session).claim(
                result_id, self._lease_seconds
            )
            if claimed is None:
                await session.rollback()
                return None
            experiment = await session.get(Experiment, claimed.experiment_id)
            if experiment is None:  # cannot happen: FK cascade
                await session.rollback()
                return None
            if claimed.started_at is None:  # the claim always sets it
                raise RuntimeError(
                    f"claimed result {result_id} has no started_at"
                )
            work = _Work(
                experiment_id=claimed.experiment_id,
                target=ModelTargetSchema(
                    provider=ProviderName(claimed.provider),
                    model=claimed.model,
                ),
                prompt=claimed.rendered_prompt,
                config=ExperimentConfigSchema.model_validate(experiment.config),
                attempts=claimed.attempts,
                claimed_started_at=claimed.started_at,
            )
            # The scope commits on exit, before the model call, so
            # concurrent claimers see the claim.
        logger.info("result %s claimed (attempt %d)", result_id, work.attempts)
        return work

    async def _execute(self, work: _Work) -> _Outcome:
        config = work.config
        # ``attempts`` is the cumulative number of model requests stored in
        # the database; ``made`` counts this run only, so a reclaimed result
        # still gets its full retry budget.
        attempts = work.attempts
        made = 1
        retries = 0
        while True:
            try:
                async with self._semaphore(work.target.provider.value):
                    response = await self._llm.generate(
                        work.target,
                        work.prompt,
                        system_prompt=config.system_prompt,
                        temperature=config.temperature,
                        max_tokens=config.max_tokens,
                        timeout_seconds=config.timeout_seconds,
                    )
                return self._success(response, attempts)
            except LLMProviderError as exc:
                if exc.retryable and made < config.max_attempts:
                    delay = backoff_delay(
                        retries, exc.retry_after_seconds, self._jitter()
                    )
                    retries += 1
                    attempts += 1
                    made += 1
                    await self._sleep(delay)
                    continue
                return self._failure(
                    exc.kind.value,
                    f"Provider request failed ({exc.kind.value})",
                    attempts,
                    request_id=exc.request_id,
                )
            except (
                ProviderNotConfiguredError,
                UnsupportedProviderError,
            ) as exc:
                setting = getattr(exc, "setting", None)
                message = (
                    f"Provider is not configured: set {setting}"
                    if setting
                    else "Provider is not supported"
                )
                return self._failure(KIND_CONFIGURATION, message, attempts)
            except Exception as exc:
                # Only the class name: the message may hold request content.
                return self._failure(
                    KIND_INTERNAL,
                    f"Unexpected error: {type(exc).__name__}",
                    attempts,
                )

    def _success(self, response: LLMResponseSchema, attempts: int) -> _Outcome:
        usage = response.usage
        text, truncated = _bound_response(
            response.text, self._max_response_chars
        )
        if truncated:
            # Counts only: the content is untrusted and never logged.
            logger.warning(
                "response truncated (original_chars=%d, stored_chars=%d)",
                len(response.text),
                len(text),
            )
        return _Outcome(
            ResultStatus.SUCCEEDED,
            {
                "attempts": attempts,
                "response_text": text,
                "response_truncated": truncated,
                "finish_reason": _clean(
                    response.finish_reason, FINISH_REASON_MAX
                ),
                "provider_request_id": _clean(
                    response.provider_request_id, IDENTIFIER_MAX
                ),
                "reported_model": _clean(
                    response.reported_model, IDENTIFIER_MAX
                ),
                # None stays None: unknown is not zero.
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "latency_ms": response.latency_ms,
                "effective_max_tokens": response.effective_max_tokens,
                "error_kind": None,
                "error_message": None,
            },
        )

    @staticmethod
    def _failure(
        kind: str,
        message: str,
        attempts: int,
        request_id: str | None = None,
    ) -> _Outcome:
        return _Outcome(
            ResultStatus.FAILED,
            {
                "attempts": attempts,
                "error_kind": kind,
                "error_message": message,
                "provider_request_id": _clean(request_id, IDENTIFIER_MAX),
            },
        )

    async def _persist_safely(
        self, result_id: uuid.UUID, work: _Work, outcome: _Outcome
    ) -> None:
        """Persist the outcome; if the database rejects it, store a generic
        failure so the result never stays running."""
        try:
            stored = await self._persist(result_id, work, outcome)
        except SQLAlchemyError:
            logger.error("result %s outcome write failed", result_id)
            fallback = self._failure(
                KIND_INTERNAL,
                "Failed to store the result",
                outcome.fields.get("attempts", work.attempts),
            )
            await self._persist(result_id, work, fallback)
            return
        if stored and outcome.status == ResultStatus.SUCCEEDED:
            await self._notify_succeeded(result_id)

    async def _notify_succeeded(self, result_id: uuid.UUID) -> None:
        """Runs after the outcome transaction committed. A failing hook
        (e.g. broker down) must never change the generation result; a
        missed trigger is repaired by the evaluation backfill."""
        if self._on_succeeded is None:
            return
        try:
            await asyncio.wait_for(
                self._on_succeeded(result_id), HOOK_TIMEOUT_SECONDS
            )
        except Exception as exc:  # includes the wait_for timeout
            # Class name only: messages may carry content.
            logger.warning(
                "result %s post-generation hook failed (%s)",
                result_id,
                type(exc).__name__,
            )

    async def _persist(
        self,
        result_id: uuid.UUID,
        work: _Work,
        outcome: _Outcome,
    ) -> bool:
        """Returns whether the outcome was stored (False: stale write)."""
        async with self._scope() as session:
            experiments = ExperimentRepository(session)
            results = ExperimentResultRepository(session)
            # Experiment lock first, like every other writer.
            experiment = await experiments.get_for_update_unscoped(
                work.experiment_id
            )
            written = await results.write_outcome(
                result_id,
                outcome.status,
                outcome.fields,
                work.claimed_started_at,
            )
            if written and experiment is not None:
                await finalize_experiment(experiment, results)
            # The scope commits the outcome and the finalization together.
        logger.info(
            "result %s %s (stored=%s, error_kind=%s)",
            result_id,
            outcome.status.value,
            written,
            outcome.fields.get("error_kind"),
        )
        return written
