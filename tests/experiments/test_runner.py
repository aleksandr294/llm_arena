"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from components.experiments import runner as runner_module
from components.experiments.enums import ExperimentStatus, ResultStatus
from components.experiments.models import Experiment, ExperimentResult
from components.experiments.repository import ExperimentResultRepository
from components.experiments.runner import ExperimentRunner
from components.experiments.service import ExperimentService
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
) -> ExperimentRunner:
    return ExperimentRunner(
        factory,
        llm.as_service(),
        max_concurrency,
        lease_seconds=lease_seconds,
        sleep=sleeps or Sleeps(),
        jitter=lambda: 0.0,
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


async def test_retry_after_is_capped(session_factory: Factory) -> None:
    llm = FakeLLM(
        [
            provider_error(LLMErrorKind.RATE_LIMIT, retry_after=120),
            provider_error(LLMErrorKind.TIMEOUT),
            response(),
        ]
    )
    sleeps = Sleeps()
    await _one(session_factory, llm, sleeps)
    assert sleeps.delays == [30.0, 2.0]


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
        service = ExperimentService(s, llm.as_service(), enqueued)
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


async def test_backoff_does_not_hold_the_semaphore(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=2, start=True)
    ids = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    llm = FakeLLM([provider_error(LLMErrorKind.TIMEOUT)])
    release = asyncio.Event()
    in_sleep = asyncio.Event()

    async def sleep(delay: float) -> None:
        in_sleep.set()
        await release.wait()

    runner = ExperimentRunner(
        session_factory,
        llm.as_service(),
        1,
        sleep=sleep,
        jitter=lambda: 0.0,
    )
    first = asyncio.create_task(runner.run(ids[0]))
    await in_sleep.wait()
    # The sleeping task must not block the only slot.
    await asyncio.wait_for(runner.run(ids[1]), timeout=5)
    release.set()
    await first
    assert (await _row(session_factory, ids[0])).attempts == 2


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
        service = ExperimentService(s, llm.as_service(), Enqueued())
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


async def test_reclaimed_result_keeps_full_retry_budget(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1, start=True)
    (result_id,) = await _ids(
        session_factory, experiment_id, ResultStatus.PENDING
    )
    async with session_factory() as s:
        await s.execute(
            text(
                "update experiment_results set status = 'running', "
                "attempts = 3, started_at = now() - interval '1 hour'"
            )
        )
        await s.commit()
    llm = FakeLLM(
        [
            provider_error(LLMErrorKind.RATE_LIMIT),
            provider_error(LLMErrorKind.RATE_LIMIT),
            response(),
        ]
    )
    await _runner(session_factory, llm).run(result_id)
    row = await _row(session_factory, result_id)
    assert row.status == ResultStatus.SUCCEEDED
    # Cumulative: 3 earlier requests + 3 in this run.
    assert row.attempts == 6 and len(llm.calls) == 3


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
        session_factory,
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
        session_factory, llm.as_service(), on_succeeded=hook
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
        session_factory, llm.as_service(), on_succeeded=hook
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
        session_factory, FakeLLM().as_service(), on_succeeded=hook
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
        session_factory, FakeLLM().as_service(), on_succeeded=hang
    )
    with caplog.at_level("WARNING"):
        await asyncio.wait_for(runner.run(result_id), 5)
    row = await _row(session_factory, result_id)
    assert row.status == "succeeded"
    assert "TimeoutError" in caplog.text
