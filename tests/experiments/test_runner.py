"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from components.experiments import runner as runner_module
from components.experiments.dependencies import build_experiment_service
from components.experiments.enums import ExperimentStatus, ResultStatus
from components.experiments.models import Experiment, ExperimentResult
from components.experiments.repository import ExperimentResultRepository
from components.experiments.runner import ExperimentRunner
from components.third_party.llm.enums import LLMErrorKind, ProviderName
from components.third_party.llm.exceptions import ProviderNotConfiguredError
from components.third_party.llm.schemas import LLMUsageSchema
from tests.access import ROOT
from tests.experiments.helpers import (
    Enqueued,
    Factory,
    FakeLLM,
    make_experiment,
    provider_error,
    response,
    scope_of,
)
from tests.security.helpers import (
    CORPUS,
    MARK,
    NUL_STRIPPED,
    NUL_TEXT,
)

pytestmark = pytest.mark.integration


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _runner(
    factory: Factory,
    llm: FakeLLM,
    sleeps: Sleeps | None = None,
    max_concurrency: int = 4,
    lease_seconds: int = 600,
    **extra: Any,
) -> ExperimentRunner:
    return ExperimentRunner(
        scope_of(factory),
        llm.as_service(),
        max_concurrency,
        lease_seconds=lease_seconds,
        sleep=sleeps or Sleeps(),
        jitter=lambda: 0.0,
        **extra,
    )


async def _ids(
    factory: Factory, experiment_id: uuid.UUID, status: ResultStatus
) -> list[uuid.UUID]:
    async with factory() as s:
        return await ExperimentResultRepository(s).list_ids_by_status(
            experiment_id, status
        )


async def _row(factory: Factory, result_id: uuid.UUID) -> ExperimentResult:
    async with factory() as s:
        row = await s.get(ExperimentResult, result_id)
        assert row is not None
        return row


async def _experiment(factory: Factory, experiment_id: uuid.UUID) -> Experiment:
    async with factory() as s:
        row = await s.get(Experiment, experiment_id)
        assert row is not None
        return row


async def _one(
    factory: Factory,
    llm: FakeLLM,
    sleeps: Sleeps | None = None,
    max_attempts: int = 3,
) -> tuple[uuid.UUID, uuid.UUID]:
    experiment_id = await make_experiment(
        factory, cases=1, start=True, max_attempts=max_attempts
    )
    (result_id,) = await _ids(factory, experiment_id, ResultStatus.PENDING)
    await _runner(factory, llm, sleeps).run(result_id)
    return experiment_id, result_id


async def test_success_stores_measured_metrics(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([response("hello")])
    experiment_id, result_id = await _one(session_factory, llm)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.SUCCEEDED
    assert row.response_text == "hello"
    assert (row.input_tokens, row.output_tokens, row.total_tokens) == (3, 4, 7)
    assert row.latency_ms == 12.5
    assert row.provider_request_id == "req-1"
    assert row.reported_model == "m1-2024"
    assert row.effective_max_tokens == 256
    assert row.attempts == 1
    assert row.error_kind is None and row.finished_at is not None
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.COMPLETED
    assert experiment.finished_at is not None
    target, prompt, kwargs = llm.calls[0]
    assert prompt == "Q: in1" and target.model == "m1"
    assert kwargs["timeout_seconds"] == 60.0


async def test_unknown_usage_stays_null(session_factory: Factory) -> None:
    llm = FakeLLM([response(usage=LLMUsageSchema(input_tokens=5))])
    _, result_id = await _one(session_factory, llm)
    row = await _row(session_factory, result_id)
    assert row.input_tokens == 5
    assert row.output_tokens is None and row.total_tokens is None


async def test_rate_limit_then_success(session_factory: Factory) -> None:
    llm = FakeLLM([provider_error(LLMErrorKind.RATE_LIMIT), response()])
    sleeps = Sleeps()
    _, result_id = await _one(session_factory, llm, sleeps)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.SUCCEEDED and row.attempts == 2
    assert sleeps.delays == [1.0]
    assert len(llm.calls) == 2


async def test_retry_after_is_honored(session_factory: Factory) -> None:
    llm = FakeLLM(
        [
            provider_error(LLMErrorKind.RATE_LIMIT, retry_after=60),
            provider_error(LLMErrorKind.TIMEOUT),
            response(),
        ]
    )
    sleeps = Sleeps()
    await _one(session_factory, llm, sleeps)
    assert sleeps.delays == [60.0, 2.0]


async def test_retry_after_above_the_cap_fails_without_sleeping(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([provider_error(LLMErrorKind.RATE_LIMIT, retry_after=600)])
    sleeps = Sleeps()
    _, result_id = await _one(session_factory, llm, sleeps)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED
    assert row.error_kind == "rate_limit" and row.attempts == 1
    assert row.provider_request_id == "req-err"
    assert sleeps.delays == [] and len(llm.calls) == 1


async def test_jitter_applies_at_the_retry_after_cap(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([provider_error(LLMErrorKind.RATE_LIMIT, retry_after=120)])
    sleeps = Sleeps()
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    runner = ExperimentRunner(
        scope_of(session_factory),
        llm.as_service(),
        sleep=sleeps,
        jitter=lambda: 0.25,
    )
    await runner.run(result_id)
    assert sleeps.delays == [120.25]


async def test_authentication_failure_is_not_retried(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([provider_error(LLMErrorKind.AUTHENTICATION)])
    sleeps = Sleeps()
    experiment_id, result_id = await _one(session_factory, llm, sleeps)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED and row.attempts == 1
    assert row.error_kind == "authentication"
    assert row.provider_request_id == "req-err"
    assert "secret prompt text" not in (row.error_message or "")
    assert sleeps.delays == [] and len(llm.calls) == 1
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.FAILED


async def test_exhausted_attempts(session_factory: Factory) -> None:
    llm = FakeLLM([provider_error(LLMErrorKind.SERVER_ERROR)] * 5)
    sleeps = Sleeps()
    _, result_id = await _one(session_factory, llm, sleeps, max_attempts=3)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED and row.attempts == 3
    assert row.error_kind == "server_error"
    assert sleeps.delays == [1.0, 2.0] and len(llm.calls) == 3


async def test_unexpected_exception_is_sanitized(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([RuntimeError("leaked prompt sk-secret")])
    sleeps = Sleeps()
    _, result_id = await _one(session_factory, llm, sleeps)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED and row.attempts == 1
    assert row.error_kind == "internal"
    assert row.error_message == "Unexpected error: RuntimeError"
    assert sleeps.delays == []


async def test_provider_not_configured_is_not_retried(
    session_factory: Factory,
) -> None:
    llm = FakeLLM(
        [ProviderNotConfiguredError(ProviderName.OPENAI, "OPENAI_API_KEY")]
    )
    _, result_id = await _one(session_factory, llm)
    row = await _row(session_factory, result_id)
    assert row.error_kind == "configuration" and row.attempts == 1
    assert "OPENAI_API_KEY" in (row.error_message or "")
    assert len(llm.calls) == 1


async def test_duplicate_delivery_makes_one_request(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([response("first")])
    _, result_id = await _one(session_factory, llm)
    before = await _row(session_factory, result_id)
    await _runner(session_factory, llm).run(result_id)
    after = await _row(session_factory, result_id)
    assert len(llm.calls) == 1
    assert (after.response_text, after.attempts, after.finished_at) == (
        before.response_text,
        before.attempts,
        before.finished_at,
    )


async def test_concurrent_duplicate_delivery_makes_one_request(
    session_factory: Factory,
) -> None:
    llm = FakeLLM(delay=0.05)
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    runner = _runner(session_factory, llm)
    await asyncio.gather(*(runner.run(result_id) for _ in range(5)))
    assert len(llm.calls) == 1


async def test_running_result_within_lease_is_not_taken(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    llm = FakeLLM()
    async with session_factory() as s:
        row = await s.get(ExperimentResult, result_id)
        assert row is not None
        row.status = "running"
        await s.execute(
            text("update experiment_results set started_at = now()")
        )
        await s.commit()
    await _runner(session_factory, llm).run(result_id)
    assert llm.calls == []


async def test_claim_without_started_at_is_an_internal_error(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    original = ExperimentResultRepository.claim

    async def claim_without_start(
        self: ExperimentResultRepository, *args: Any, **kwargs: Any
    ) -> ExperimentResult | None:
        row = await original(self, *args, **kwargs)
        assert row is not None
        row.started_at = None
        return row

    monkeypatch.setattr(
        ExperimentResultRepository, "claim", claim_without_start
    )
    llm = FakeLLM()

    with pytest.raises(RuntimeError, match="has no started_at"):
        await _runner(session_factory, llm).run(result_id)

    assert llm.calls == []


async def test_stale_lease_is_reclaimed(session_factory: Factory) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    async with session_factory() as s:
        await s.execute(
            text(
                "update experiment_results set status = 'running', "
                "attempts = 1, started_at = now() - interval '1 hour'"
            )
        )
        await s.commit()
    llm = FakeLLM()
    await _runner(session_factory, llm).run(result_id)
    row = await _row(session_factory, result_id)
    assert len(llm.calls) == 1
    assert row.status == ResultStatus.SUCCEEDED and row.attempts == 2


async def test_partial_failure_keeps_successes(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=3, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    llm = FakeLLM(
        [
            response("a"),
            provider_error(LLMErrorKind.INVALID_REQUEST),
            response("c"),
        ]
    )
    runner = _runner(session_factory, llm)
    for result_id in ids:
        await runner.run(result_id)
    rows = [await _row(session_factory, i) for i in ids]
    assert [r.status for r in rows] == ["succeeded", "failed", "succeeded"]
    assert [r.response_text for r in rows] == ["a", None, "c"]
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.PARTIALLY_FAILED


async def test_retry_failed_reruns_only_failed(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=3, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    llm = FakeLLM([response("a"), provider_error(LLMErrorKind.INVALID_REQUEST)])
    runner = _runner(session_factory, llm)
    for result_id in ids:
        await runner.run(result_id)
    enqueued = Enqueued()
    async with session_factory() as s:
        service = build_experiment_service(s, llm.as_service(), enqueued)
        assert await service.retry_failed(ROOT, experiment_id) == 1
    assert enqueued.flat == [ids[1]]
    calls_before = len(llm.calls)
    for result_id in ids:  # redelivering everything must be harmless
        await runner.run(result_id)
    assert len(llm.calls) == calls_before + 1
    rows = [await _row(session_factory, i) for i in ids]
    assert [r.status for r in rows] == ["succeeded"] * 3
    assert rows[0].response_text == "a"
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.COMPLETED


async def test_concurrency_limit_is_never_exceeded(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=10, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    llm = FakeLLM(delay=0.02)
    runner = _runner(session_factory, llm, max_concurrency=2)
    await asyncio.gather(*(runner.run(i) for i in ids))
    assert len(llm.calls) == 10
    assert llm.max_in_flight == 2
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.COMPLETED


async def test_backoff_holds_the_provider_slot(
    session_factory: Factory,
) -> None:
    """Backpressure: a result waiting out a rate limit keeps its slot, so
    the next result is neither claimed nor sent to the provider."""
    experiment_id = await make_experiment(session_factory, cases=2, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    llm = FakeLLM([provider_error(LLMErrorKind.TIMEOUT)])
    release = asyncio.Event()
    in_sleep = asyncio.Event()

    async def sleep(delay: float) -> None:
        in_sleep.set()
        await release.wait()

    runner = ExperimentRunner(
        scope_of(session_factory),
        llm.as_service(),
        1,
        sleep=sleep,
        jitter=lambda: 0.0,
    )
    first = asyncio.create_task(runner.run(ids[0]))
    await in_sleep.wait()
    second = asyncio.create_task(runner.run(ids[1]))
    await asyncio.sleep(0.2)
    assert not second.done()
    assert (await _row(session_factory, ids[1])).status == "pending"
    assert len(llm.calls) == 1
    release.set()
    await asyncio.gather(first, second)
    assert (await _row(session_factory, ids[0])).attempts == 2
    assert (await _row(session_factory, ids[1])).status == "succeeded"


async def test_waiting_results_are_not_claimed_and_keep_their_lease(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=3, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    started = asyncio.Event()
    release = asyncio.Event()
    llm = FakeLLM()

    async def handler(prompt: str) -> Any:
        started.set()
        await release.wait()
        return response()

    llm.handler = handler
    runner = _runner(session_factory, llm, max_concurrency=1)
    tasks = [asyncio.create_task(runner.run(i)) for i in ids]
    await started.wait()
    await asyncio.sleep(0.2)
    rows = [await _row(session_factory, i) for i in ids]
    assert sorted(r.status for r in rows) == ["pending", "pending", "running"]
    waiting = [r for r in rows if r.status == "pending"]
    assert all(r.started_at is None and r.attempts == 0 for r in waiting)
    assert len(llm.calls) == 1
    release.set()
    await asyncio.gather(*tasks)
    rows = [await _row(session_factory, i) for i in ids]
    assert [r.status for r in rows] == ["succeeded"] * 3
    assert [r.attempts for r in rows] == [1, 1, 1]
    assert len(llm.calls) == 3


async def test_skipped_deliveries_log_the_reason(
    session_factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    llm = FakeLLM()
    _, finished_id = await _one(session_factory, llm)
    owned_experiment = await make_experiment(
        session_factory, cases=1, start=True
    )
    (owned_id,) = await _ids(
        session_factory, owned_experiment, ResultStatus.PENDING
    )
    async with session_factory() as s:
        await s.execute(
            text(
                "update experiment_results set status = 'running', "
                "started_at = now() where id = :id"
            ),
            {"id": owned_id},
        )
        await s.commit()
    runner = _runner(session_factory, llm)
    with caplog.at_level("INFO"):
        await runner.run(finished_id)
        await runner.run(owned_id)
        await runner.run(uuid.uuid4())
    assert f"result {finished_id} skipped (finished)" in caplog.text
    assert f"result {owned_id} skipped (owned)" in caplog.text
    assert "skipped (not found)" in caplog.text
    assert len(llm.calls) == 1


async def test_unknown_provider_fails_the_claimed_result(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    async with session_factory() as s:
        await s.execute(
            text("update experiment_results set provider = 'nonesuch'")
        )
        await s.commit()
    llm = FakeLLM()
    await _runner(session_factory, llm).run(result_id)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED
    assert row.error_kind == "configuration" and row.attempts == 1
    assert "ValueError" in (row.error_message or "")
    assert llm.calls == []
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.FAILED


async def test_invalid_stored_config_fails_the_claimed_result(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    async with session_factory() as s:
        await s.execute(
            text("update experiments set config = '{\"temperature\": 99}'")
        )
        await s.commit()
    llm = FakeLLM()
    await _runner(session_factory, llm).run(result_id)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED
    assert row.error_kind == "configuration"
    assert "ValidationError" in (row.error_message or "")
    assert "99" not in (row.error_message or "")
    assert llm.calls == []


async def test_cancelled_worker_marks_the_result_interrupted(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    started = asyncio.Event()
    llm = FakeLLM()

    async def handler(prompt: str) -> Any:
        started.set()
        await asyncio.Event().wait()

    llm.handler = handler
    task = asyncio.create_task(_runner(session_factory, llm).run(result_id))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED
    assert row.error_kind == "interrupted" and row.attempts == 1
    assert row.error_message == "Worker stopped before the request finished"
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.FAILED
    assert experiment.finished_at is not None
    # retry-failed re-runs it like any failed result.
    enqueued = Enqueued()
    async with session_factory() as s:
        service = build_experiment_service(s, FakeLLM().as_service(), enqueued)
        assert await service.retry_failed(ROOT, experiment_id) == 1
        await s.commit()
    assert enqueued.flat == [result_id]
    await _runner(session_factory, FakeLLM()).run(result_id)
    assert (await _row(session_factory, result_id)).status == "succeeded"


async def test_cancel_during_backoff_reports_attempts_made(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    in_sleep = asyncio.Event()

    async def sleep(delay: float) -> None:
        in_sleep.set()
        await asyncio.Event().wait()

    llm = FakeLLM([provider_error(LLMErrorKind.TIMEOUT)])
    runner = ExperimentRunner(
        scope_of(session_factory), llm.as_service(), sleep=sleep
    )
    task = asyncio.create_task(runner.run(result_id))
    await in_sleep.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await _row(session_factory, result_id)
    assert row.error_kind == "interrupted" and row.attempts == 2


async def test_cancel_does_not_overwrite_a_newer_claim(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    started = asyncio.Event()
    llm = FakeLLM()

    async def handler(prompt: str) -> Any:
        started.set()
        await asyncio.Event().wait()

    llm.handler = handler
    task = asyncio.create_task(_runner(session_factory, llm).run(result_id))
    await started.wait()
    async with session_factory() as s:  # another worker took the lease over
        await s.execute(
            text(
                "update experiment_results set started_at = now() "
                "+ interval '1 minute'"
            )
        )
        await s.commit()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await _row(session_factory, result_id)).status == "running"


async def test_cancel_with_running_result_finishes_as_cancelled(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=3, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    started = asyncio.Event()
    release = asyncio.Event()
    llm = FakeLLM()

    async def handler(prompt: str):  # type: ignore[no-untyped-def]
        started.set()
        await release.wait()
        return response("late")

    llm.handler = handler
    runner = _runner(session_factory, llm)
    task = asyncio.create_task(runner.run(ids[0]))
    await started.wait()
    async with session_factory() as s:
        service = build_experiment_service(s, llm.as_service(), Enqueued())
        experiment = await service.cancel(ROOT, experiment_id)
        await s.commit()
        assert experiment.status == ExperimentStatus.RUNNING
    release.set()
    await task
    await runner.run(ids[1])  # cancelled pending result: no-op
    assert len(llm.calls) == 1
    first = await _row(session_factory, ids[0])
    assert first.status == ResultStatus.SUCCEEDED
    assert first.response_text == "late"
    assert (await _row(session_factory, ids[1])).status == "cancelled"
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.CANCELLED
    assert experiment.finished_at is not None


async def test_untrusted_provider_values_are_sanitized(
    session_factory: Factory,
) -> None:
    reply = response("a\x00b").model_copy(
        update={
            "provider_request_id": "r" * 300 + "\x00",
            "reported_model": "m" * 300,
            "finish_reason": "f" * 100,
        }
    )
    _, result_id = await _one(session_factory, FakeLLM([reply]))
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.SUCCEEDED
    assert row.response_text == "ab"
    assert row.provider_request_id == "r" * 255
    assert row.reported_model == "m" * 255
    assert row.finish_reason == "f" * 64


async def test_outcome_write_error_stores_internal_failure(
    session_factory: Factory,
) -> None:
    # Out of range for the integer column: the database rejects the write.
    reply = response(usage=LLMUsageSchema(input_tokens=2**40))
    experiment_id, result_id = await _one(session_factory, FakeLLM([reply]))
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.FAILED
    assert row.error_kind == "internal"
    assert row.error_message == "Failed to store the result"
    assert row.response_text is None
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.FAILED


async def _reclaim(
    factory: Factory, attempts: int, llm: FakeLLM
) -> ExperimentResult:
    experiment_id = await make_experiment(factory, cases=1, start=True)
    (result_id,) = await _ids(factory, experiment_id, ResultStatus.PENDING)
    async with factory() as s:
        await s.execute(
            text(
                "update experiment_results set status = 'running', "
                f"attempts = {attempts}, "
                "started_at = now() - interval '1 hour'"
            )
        )
        await s.commit()
    await _runner(factory, llm).run(result_id)
    return await _row(factory, result_id)


async def test_retry_budget_spans_reclaims(session_factory: Factory) -> None:
    # 2 of 3 attempts used before the crash; the reclaim is the third.
    llm = FakeLLM([provider_error(LLMErrorKind.RATE_LIMIT)] * 3)
    row = await _reclaim(session_factory, 2, llm)
    assert row.status == ResultStatus.FAILED
    assert row.attempts == 3 and len(llm.calls) == 1


async def test_result_over_budget_makes_one_request_and_no_retries(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([provider_error(LLMErrorKind.RATE_LIMIT)] * 3)
    row = await _reclaim(session_factory, 3, llm)
    assert row.status == ResultStatus.FAILED
    assert row.attempts == 4 and len(llm.calls) == 1


async def test_reclaimed_result_can_still_succeed(
    session_factory: Factory,
) -> None:
    llm = FakeLLM([response("again")])
    row = await _reclaim(session_factory, 2, llm)
    assert row.status == ResultStatus.SUCCEEDED and row.attempts == 3


class Hook:
    def __init__(self, fail: bool = False) -> None:
        self.ids: list[uuid.UUID] = []
        self.fail = fail

    async def __call__(self, result_id: uuid.UUID) -> None:
        self.ids.append(result_id)
        if self.fail:
            raise RuntimeError("broker down: secret")


async def test_hook_called_once_after_succeeded_outcome(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=2, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    hook = Hook()
    runner = ExperimentRunner(
        scope_of(session_factory),
        FakeLLM().as_service(),
        on_succeeded=hook,
        jitter=lambda: 0.0,
    )
    await runner.run(ids[0])
    await runner.run(ids[0])  # duplicate delivery: not claimable, no hook
    assert hook.ids == [ids[0]]
    # The hook sees a committed, succeeded row.
    assert (await _row(session_factory, ids[0])).status == "succeeded"


async def test_hook_not_called_for_failure(session_factory: Factory) -> None:
    experiment_id = await make_experiment(
        session_factory, cases=1, max_attempts=1, start=True
    )
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    hook = Hook()
    llm = FakeLLM([provider_error(LLMErrorKind.AUTHENTICATION)])
    runner = ExperimentRunner(
        scope_of(session_factory), llm.as_service(), on_succeeded=hook
    )
    await runner.run(result_id)
    assert hook.ids == []
    assert (await _row(session_factory, result_id)).status == "failed"


async def test_hook_not_called_for_stale_write(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    hook = Hook()
    llm = FakeLLM()

    async def steal(prompt: str) -> Any:
        # Another worker re-claimed the result while this call was running.
        async with session_factory() as s:
            await s.execute(
                text(
                    "update experiment_results set started_at = now() "
                    "+ interval '1 minute'"
                )
            )
            await s.commit()
        return response()

    llm.handler = steal
    runner = ExperimentRunner(
        scope_of(session_factory), llm.as_service(), on_succeeded=hook
    )
    await runner.run(result_id)
    assert hook.ids == []


async def test_failing_hook_does_not_change_the_result(
    session_factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    hook = Hook(fail=True)
    runner = ExperimentRunner(
        scope_of(session_factory), FakeLLM().as_service(), on_succeeded=hook
    )
    with caplog.at_level("WARNING"):
        await runner.run(result_id)
    row = await _row(session_factory, result_id)
    assert (row.status, row.response_text) == ("succeeded", "ok")
    assert (await _experiment(session_factory, experiment_id)).status == (
        ExperimentStatus.COMPLETED
    )
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


async def test_hanging_hook_is_bounded(
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module, "HOOK_TIMEOUT_SECONDS", 0.05)
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )

    async def hang(result_id: uuid.UUID) -> None:
        await asyncio.Event().wait()

    runner = ExperimentRunner(
        scope_of(session_factory), FakeLLM().as_service(), on_succeeded=hang
    )
    with caplog.at_level("WARNING"):
        await asyncio.wait_for(runner.run(result_id), 5)
    row = await _row(session_factory, result_id)
    assert row.status == "succeeded"
    assert "TimeoutError" in caplog.text


async def test_no_session_or_transaction_is_open_during_the_model_call(
    session_factory: Factory,
) -> None:
    """The claim is committed and the outcome not yet written while the
    model call is in flight: no scope is open and the database shows no
    transaction left open by the runner's sessions."""
    open_scopes = 0
    inner = scope_of(session_factory)

    @asynccontextmanager
    async def counting_scope() -> AsyncIterator[AsyncSession]:
        nonlocal open_scopes
        open_scopes += 1
        try:
            async with inner() as session:
                yield session
        finally:
            open_scopes -= 1

    observed: dict[str, Any] = {}
    llm = FakeLLM()

    async def handler(prompt: str) -> Any:
        observed["open_scopes"] = open_scopes
        async with session_factory() as other:
            observed["idle_in_transaction"] = await other.scalar(
                text(
                    "select count(*) from pg_stat_activity "
                    "where datname = current_database() "
                    "and pid <> pg_backend_pid() "
                    "and application_name = "
                    "current_setting('application_name') "
                    "and state like 'idle in transaction%'"
                )
            )
            row = await other.scalar(
                text("select status from experiment_results")
            )
            observed["status"] = row
        return response()

    llm.handler = handler
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    runner = ExperimentRunner(
        counting_scope, llm.as_service(), jitter=lambda: 0.0
    )
    await runner.run(result_id)
    assert observed == {
        "open_scopes": 0,
        "idle_in_transaction": 0,
        "status": "running",  # the claim is already committed
    }
    assert (await _row(session_factory, result_id)).status == "succeeded"


@pytest.mark.parametrize(
    ("length", "truncated"),
    [(5, False), (99, False), (100, False), (101, True), (5000, True)],
)
async def test_response_is_bounded_with_marker_and_flag(
    session_factory: Factory, length: int, truncated: bool
) -> None:
    limit = 100
    text = "x" * length
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    runner = _runner(
        session_factory, FakeLLM([response(text)]), max_response_chars=limit
    )
    await runner.run(result_id)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.SUCCEEDED
    assert row.response_truncated is truncated
    assert row.response_text is not None
    marker = runner_module.TRUNCATION_MARKER
    if truncated:
        assert len(row.response_text) == limit  # marker included
        assert row.response_text.endswith(marker)
        assert row.response_text.startswith("x" * (limit - len(marker)))
    else:
        assert row.response_text == text


@pytest.mark.parametrize(
    "sample", [*CORPUS.values(), NUL_TEXT], ids=[*CORPUS, "nul"]
)
async def test_hostile_provider_text_is_stored_inert_and_never_logged(
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
    sample: str,
) -> None:
    with caplog.at_level("DEBUG"):
        _, result_id = await _one(session_factory, FakeLLM([response(sample)]))
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.SUCCEEDED
    assert row.response_text == (NUL_STRIPPED if sample == NUL_TEXT else sample)
    assert MARK not in caplog.text


async def test_lone_surrogate_is_stored_as_replacement_character(
    session_factory: Factory,
) -> None:
    reply = response("a\ud800b").model_copy(
        update={"reported_model": "m\udc00"}
    )
    _, result_id = await _one(session_factory, FakeLLM([reply]))
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.SUCCEEDED
    assert row.response_text == "a\ufffdb"
    assert row.reported_model == "m\ufffd"


async def test_both_outcome_writes_failing_is_logged_and_raised(
    session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )

    async def broken(*args: Any, **kwargs: Any) -> bool:
        raise OperationalError("insert secret-statement", {}, Exception("db"))

    monkeypatch.setattr(ExperimentRunner, "_persist", broken)
    runner = _runner(session_factory, FakeLLM([response("hostile " + MARK)]))
    with caplog.at_level("ERROR"), pytest.raises(OperationalError):
        await runner.run(result_id)
    assert str(result_id) in caplog.text
    assert "fallback write failed" in caplog.text
    assert "OperationalError" in caplog.text
    assert MARK not in caplog.text and "secret-statement" not in caplog.text
    # Left running: the Taskiq failure and the lease reclaim take over.
    assert (await _row(session_factory, result_id)).status == "running"
