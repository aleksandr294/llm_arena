"""Pure helpers: prompt rendering, final-status derivation and backoff.

Kept free of I/O so they are unit-testable without a database or a clock.
"""

import re
from collections.abc import Mapping

from components.experiments.enums import ExperimentStatus, ResultStatus

INPUT_TOKEN = (
    "{input}"  # nosec B105 - prompt template placeholder, not a password
)
EXPECTED_OUTPUT_TOKEN = "{expected_output}"  # nosec B105 - prompt template placeholder, not a password
# Bounds on rendered prompts, checked before any prompt is built. A template
# may repeat ``{input}`` many times over many large cases, so the sizes of
# the schema fields alone do not bound what one request stores. Constants
# rather than settings: they protect the service itself and the service
# takes no settings; 200k characters fits a maximal template plus a maximal
# input and expected output.
MAX_RENDERED_PROMPT_CHARS = 200_000
MAX_TOTAL_RENDERED_CHARS = 100_000_000
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


def rendered_length(
    template: str, input_text: str, expected_output: str | None
) -> int:
    """Length ``render_prompt`` would produce, without building the string."""
    inputs = template.count(INPUT_TOKEN)
    expecteds = template.count(EXPECTED_OUTPUT_TOKEN)
    return (
        len(template)
        - inputs * len(INPUT_TOKEN)
        - expecteds * len(EXPECTED_OUTPUT_TOKEN)
        + inputs * len(input_text)
        + expecteds * len(expected_output or "")
    )


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
