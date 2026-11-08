"""Executes one experiment result.

All state lives in database rows. The runner first waits for a provider
request slot (so waiting never consumes the result's lease), then runs a
result as three atomic operations, each in its own ``scope()`` session and
transaction: the claim (committed before the model call so concurrent
claimers see it), the model call with no session and no transaction open,
and the conditional write of the outcome together with the experiment
finalization. Logs carry ids,
status, attempt and error kind only, never prompts, responses, keys or
provider messages.
"""

import asyncio
import logging
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from components.core.config import DEFAULT_MAX_STORED_RESPONSE_CHARS
from components.core.database import SessionScope, rollback
from components.core.exceptions import PricingConfigError
from components.core.pricing import estimate_cost, find_rate, load_pricing
from components.core.utils import RETRY_AFTER_CAP_SECONDS, backoff_delay
from components.experiments.enums import ResultStatus
from components.experiments.model_metrics import (
    record_cost,
    record_generation_speed,
    record_latency,
)
from components.experiments.models import Experiment
from components.experiments.repository import (
    LEASE_SECONDS,
    ExperimentRepository,
    ExperimentResultRepository,
    finalize_experiment,
)
from components.experiments.schemas import ExperimentConfigSchema
from components.third_party.llm.enums import LLMErrorKind, ProviderName
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
KIND_INTERRUPTED = "interrupted"
JITTER_MAX_SECONDS = 0.5
HOOK_TIMEOUT_SECONDS = 10.0
# Column sizes of experiment_results; provider values are untrusted.
FINISH_REASON_MAX = 64
IDENTIFIER_MAX = 255
# Appended to a stored response that was cut at the size limit.
TRUNCATION_MARKER = "\n[... response truncated ...]"
# PostgreSQL text cannot hold NUL, and a lone surrogate cannot be encoded
# as UTF-8, so either one would make the outcome write fail.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_FINISHED = frozenset(
    {
        ResultStatus.SUCCEEDED.value,
        ResultStatus.FAILED.value,
        ResultStatus.CANCELLED.value,
    }
)

Sleep = Callable[[float], Awaitable[None]]
# Called with the result id after a succeeded outcome was stored.
OnSucceeded = Callable[[uuid.UUID], Awaitable[None]]


@dataclass(frozen=True)
class _Claim:
    """Plain snapshot of a claimed result, so no ORM object is used while no
    session is open. Provider and config are still unvalidated strings and
    dicts: an unusable one must fail the result, not abort the claim."""

    experiment_id: uuid.UUID
    provider: str
    model: str
    prompt: str
    config: dict[str, Any]
    attempts: int
    claimed_started_at: datetime


@dataclass(frozen=True)
class _Work:
    """What a model call needs, validated."""

    target: ModelTargetSchema
    prompt: str
    config: ExperimentConfigSchema


@dataclass
class _Progress:
    """Cumulative requests stored for the claimed result, kept current so an
    interrupted run reports the attempts it really made."""

    attempts: int


@dataclass(frozen=True)
class _Outcome:
    status: ResultStatus
    fields: dict[str, Any]


def _sanitize(value: str) -> str:
    """Drop NUL bytes and replace lone surrogates with U+FFFD."""
    return _LONE_SURROGATE.sub("\ufffd", value.replace("\x00", ""))


def _clean(value: str | None, max_length: int) -> str | None:
    """Make untrusted text storable and fit the column."""
    if value is None:
        return None
    return _sanitize(value)[:max_length]


def _default_jitter() -> float:
    return random.uniform(  # noqa: S311 - non-cryptographic retry jitter
        0, JITTER_MAX_SECONDS
    )


def _bound_response(text: str, limit: int) -> tuple[str, bool]:
    """Make the text storable and fit ``limit`` characters, marker
    included. Returns the text and whether it was cut; storable text within
    the limit is returned unchanged."""
    text = _sanitize(text)
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
        pricing_file: str | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if max_response_chars < 1:
            raise ValueError("max_response_chars must be at least 1")
        self._max_response_chars = max_response_chars
        self._pricing_file = pricing_file
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
        """Execute one result; a no-op when it is owned or finished.

        The provider slot is taken before the claim and held through the
        retries, so the lease measures only real work and a rate-limited
        provider is not hit by results that are merely waiting."""
        provider = await self._peek(result_id)
        if provider is None:
            return
        claim: _Claim | None = None
        progress = _Progress(0)
        try:
            async with self._semaphore(provider):
                claim = await self._claim(result_id)
                if claim is None:
                    return
                progress = _Progress(claim.attempts)
                outcome = await self._generate(claim, progress)
            await self._persist_safely(result_id, claim, outcome)
        except asyncio.CancelledError:
            if claim is not None:
                await self._persist_interrupted(result_id, claim, progress)
            raise

    async def _peek(self, result_id: uuid.UUID) -> str | None:
        """Provider of a deliverable result, without claiming it."""
        async with self._scope() as session:
            found = await ExperimentResultRepository(session).get_provider(
                result_id
            )
        if found is None:
            logger.info("result %s skipped (not found)", result_id)
            return None
        provider, status = found
        if status in _FINISHED:
            logger.info("result %s skipped (finished)", result_id)
            return None
        return provider

    async def _claim(self, result_id: uuid.UUID) -> _Claim | None:
        async with self._scope() as session:
            results = ExperimentResultRepository(session)
            claimed = await results.claim(result_id, self._lease_seconds)
            if claimed is None:
                found = await results.get_provider(result_id)
                await rollback(session)
                reason = (
                    "finished" if found and found[1] in _FINISHED else "owned"
                )
                logger.info("result %s skipped (%s)", result_id, reason)
                return None
            experiment = await session.get(Experiment, claimed.experiment_id)
            if experiment is None:  # cannot happen: FK cascade
                await rollback(session)
                return None
            if claimed.started_at is None:  # the claim always sets it
                raise RuntimeError(
                    f"claimed result {result_id} has no started_at"
                )
            claim = _Claim(
                experiment_id=claimed.experiment_id,
                provider=claimed.provider,
                model=claimed.model,
                prompt=claimed.rendered_prompt,
                config=dict(experiment.config),
                attempts=claimed.attempts,
                claimed_started_at=claimed.started_at,
            )
            # The scope commits on exit, before the model call, so
            # concurrent claimers see the claim.
        logger.info("result %s claimed (attempt %d)", result_id, claim.attempts)
        return claim

    async def _generate(self, claim: _Claim, progress: _Progress) -> _Outcome:
        try:
            work = _Work(
                target=ModelTargetSchema(
                    provider=ProviderName(claim.provider), model=claim.model
                ),
                prompt=claim.prompt,
                config=ExperimentConfigSchema.model_validate(claim.config),
            )
        except (ValueError, ValidationError) as exc:
            # Only the class name: the message may hold stored content.
            return self._failure(
                KIND_CONFIGURATION,
                "Stored target or configuration is unusable "
                f"({type(exc).__name__})",
                claim.attempts,
            )
        return await self._execute(work, progress)

    async def _execute(self, work: _Work, progress: _Progress) -> _Outcome:
        config = work.config
        # ``progress.attempts`` is the cumulative number of model requests
        # stored in the database (the claim already counted this run's
        # first one), so a reclaimed result shares one retry budget.
        retries = 0
        while True:
            try:
                response = await self._llm.generate(
                    work.target,
                    work.prompt,
                    system_prompt=config.system_prompt,
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                    timeout_seconds=config.timeout_seconds,
                )
                self._record_model_metrics(work.target, response)
                return self._success(response, progress.attempts)
            except LLMProviderError as exc:
                if exc.retryable and progress.attempts < config.max_attempts:
                    asked = exc.retry_after_seconds
                    if asked is not None and asked > RETRY_AFTER_CAP_SECONDS:
                        return self._failure(
                            LLMErrorKind.RATE_LIMIT.value,
                            "Provider asked for a retry delay above the "
                            "limit (rate_limit)",
                            progress.attempts,
                            request_id=exc.request_id,
                        )
                    delay = backoff_delay(retries, asked, self._jitter())
                    retries += 1
                    progress.attempts += 1
                    await self._sleep(delay)
                    continue
                return self._failure(
                    exc.kind.value,
                    f"Provider request failed ({exc.kind.value})",
                    progress.attempts,
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
                return self._failure(
                    KIND_CONFIGURATION, message, progress.attempts
                )
            except Exception as exc:
                # Only the class name: the message may hold request content.
                return self._failure(
                    KIND_INTERNAL,
                    f"Unexpected error: {type(exc).__name__}",
                    progress.attempts,
                )

    def _record_model_metrics(
        self, target: ModelTargetSchema, response: LLMResponseSchema
    ) -> None:
        """Best-effort: a metrics problem must never affect generation."""
        provider = target.provider.value
        model = target.model
        try:
            record_latency(provider, model, response.latency_ms)
            record_generation_speed(
                provider,
                model,
                response.usage.output_tokens,
                response.latency_ms,
            )
            self._record_cost(provider, model, response)
        except Exception:
            logger.warning("model call metrics recording failed")

    def _record_cost(
        self, provider: str, model: str, response: LLMResponseSchema
    ) -> None:
        try:
            pricing = load_pricing(self._pricing_file)
        except PricingConfigError as exc:
            # The path is operator configuration, never request content.
            logger.warning(
                "Pricing file %r is unusable: %s; cost metric unavailable",
                exc.path,
                exc.reason,
            )
            return
        if pricing is None:
            return
        rate = find_rate(pricing, provider, model)
        if rate is None:
            return
        cost = estimate_cost(
            rate, response.usage.input_tokens, response.usage.output_tokens
        )
        if cost is not None:
            record_cost(provider, model, pricing.currency, cost)

    def _success(self, response: LLMResponseSchema, attempts: int) -> _Outcome:
        usage = response.usage
        cleaned = _sanitize(response.text)
        text, truncated = _bound_response(cleaned, self._max_response_chars)
        if truncated:
            # Counts only: the content is untrusted and never logged.
            # ``original_chars`` is the length after cleaning, i.e. of what
            # would have been stored without the limit.
            logger.warning(
                "response truncated (original_chars=%d, stored_chars=%d)",
                len(cleaned),
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
        self, result_id: uuid.UUID, claim: _Claim, outcome: _Outcome
    ) -> None:
        """Persist the outcome; if the database rejects it, store a generic
        failure so the result never stays running. When that write fails
        too, the error is logged and re-raised so the task fails visibly and
        the lease path takes over."""
        try:
            stored = await self._persist(result_id, claim, outcome)
        except (SQLAlchemyError, UnicodeError):
            logger.error("result %s outcome write failed", result_id)
            fallback = self._failure(
                KIND_INTERNAL,
                "Failed to store the result",
                outcome.fields.get("attempts", claim.attempts),
            )
            try:
                await self._persist(result_id, claim, fallback)
            except Exception as exc:
                logger.error(
                    "result %s fallback write failed (%s)",
                    result_id,
                    type(exc).__name__,
                )
                raise
            return
        if stored and outcome.status == ResultStatus.SUCCEEDED:
            await self._notify_succeeded(result_id)

    async def _persist_interrupted(
        self, result_id: uuid.UUID, claim: _Claim, progress: _Progress
    ) -> None:
        """The worker was cancelled after the claim: fail the result so it
        is not left running until the lease expires. Shielded so the cancel
        that is being handled does not abort the write; if the write fails
        the lease reclaim still recovers the result."""
        outcome = self._failure(
            KIND_INTERRUPTED,
            "Worker stopped before the request finished",
            progress.attempts,
        )
        try:
            await asyncio.shield(
                self._persist_safely(result_id, claim, outcome)
            )
        except Exception as exc:
            logger.error(
                "result %s interrupted write failed (%s)",
                result_id,
                type(exc).__name__,
            )

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
        claim: _Claim,
        outcome: _Outcome,
    ) -> bool:
        """Returns whether the outcome was stored (False: stale write)."""
        async with self._scope() as session:
            experiments = ExperimentRepository(session)
            results = ExperimentResultRepository(session)
            # Experiment lock first, like every other writer.
            experiment = await experiments.get_for_update_unscoped(
                claim.experiment_id
            )
            written = await results.write_outcome(
                result_id,
                outcome.status,
                outcome.fields,
                claim.claimed_started_at,
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
