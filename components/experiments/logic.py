"""Pure helpers: prompt rendering, final-status derivation and backoff.

Kept free of I/O so they are unit-testable without a database or a clock.
"""

import re
from collections.abc import Mapping

from components.experiments.models import ExperimentStatus, ResultStatus

INPUT_TOKEN = "{input}"
EXPECTED_OUTPUT_TOKEN = "{expected_output}"
BACKOFF_CAP_SECONDS = 30.0
BACKOFF_BASE_SECONDS = 1.0

_TOKEN_PATTERN = re.compile(
    re.escape(INPUT_TOKEN) + "|" + re.escape(EXPECTED_OUTPUT_TOKEN)
)


def render_prompt(
    template: str, input_text: str, expected_output: str | None
) -> str:
    """Replace ``{input}`` and ``{expected_output}`` in one pass.

    A single regex pass (rather than sequential ``str.replace``) guarantees
    that inserted case values are never re-scanned, so a case whose input
    contains ``{expected_output}`` stays inert. All other braces are literal;
    ``str.format`` is deliberately avoided because it allows attribute and
    index access. A missing expected output renders as an empty string.
    """
    values = {
        INPUT_TOKEN: input_text,
        EXPECTED_OUTPUT_TOKEN: expected_output or "",
    }
    return _TOKEN_PATTERN.sub(lambda m: values[m.group(0)], template)


def derive_final_status(
    counts: Mapping[str, int], cancel_requested: bool
) -> ExperimentStatus | None:
    """Return the final experiment status, or ``None`` while any result is
    still pending or running."""
    if counts.get(ResultStatus.PENDING, 0) or counts.get(
        ResultStatus.RUNNING, 0
    ):
        return None
    if cancel_requested:
        return ExperimentStatus.CANCELLED
    succeeded = counts.get(ResultStatus.SUCCEEDED, 0)
    failed = counts.get(ResultStatus.FAILED, 0)
    if failed == 0 and succeeded == 0:
        # Only cancelled results remain.
        return ExperimentStatus.CANCELLED
    if failed == 0:
        return ExperimentStatus.COMPLETED
    if succeeded == 0:
        return ExperimentStatus.FAILED
    return ExperimentStatus.PARTIALLY_FAILED


def backoff_delay(
    retry_number: int,
    retry_after_seconds: float | None = None,
    jitter_seconds: float = 0.0,
    base_seconds: float = BACKOFF_BASE_SECONDS,
    cap_seconds: float = BACKOFF_CAP_SECONDS,
) -> float:
    """Seconds to sleep before retry ``retry_number`` (0 for the first retry).

    Exponential ``base * 2**n``, or the provider's retry-after when given;
    plus caller-supplied jitter, and the total never exceeds the cap.
    """
    if retry_after_seconds is not None:
        delay = max(0.0, retry_after_seconds)
    else:
        delay = base_seconds * 2 ** max(0, retry_number)
    return min(cap_seconds, delay + max(0.0, jitter_seconds))
