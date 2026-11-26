from components.core.utils import (
    RETRY_AFTER_CAP_SECONDS,
    backoff_delay,
)


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_delay(n) for n in range(7)] == [1, 2, 4, 8, 16, 30, 30]


def test_backoff_honors_retry_after_up_to_its_cap() -> None:
    assert backoff_delay(0, retry_after_seconds=7.5) == 7.5
    assert backoff_delay(0, retry_after_seconds=60) == 60
    assert backoff_delay(0, retry_after_seconds=600) == RETRY_AFTER_CAP_SECONDS
    assert backoff_delay(3, retry_after_seconds=0) == 0
    assert backoff_delay(0, retry_after_seconds=-5) == 0


def test_backoff_jitter_is_added_after_the_cap() -> None:
    assert backoff_delay(1, jitter_seconds=0.25) == 2.25
    assert backoff_delay(5, jitter_seconds=3) == 33
    assert backoff_delay(0, retry_after_seconds=5, jitter_seconds=0.5) == 5.5
    assert (
        backoff_delay(0, retry_after_seconds=600, jitter_seconds=0.5)
        == RETRY_AFTER_CAP_SECONDS + 0.5
    )
    assert backoff_delay(0, jitter_seconds=-1) == 1
