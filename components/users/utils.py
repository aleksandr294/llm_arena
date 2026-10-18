import re
from typing import Final

from components.users.exceptions import InvalidEmailError

EMAIL_MAX_LENGTH: Final = 254
# Deliberately simple: one "@", a dotted domain, printable ASCII only (no
# whitespace, controls or non-ASCII). ASCII keeps ``lower()`` length-stable
# and free of locale surprises. Deliverability is not checked.
_LOCAL: Final = r"[\x21-\x3f\x41-\x7e]+"
_LABEL: Final = r"[\x21-\x2d\x2f-\x3f\x41-\x7e]+"
_EMAIL_PATTERN: Final = re.compile(rf"{_LOCAL}@{_LABEL}(\.{_LABEL})+")


def normalize_email(email: str) -> str:
    """Trim and validate. Case is preserved (uniqueness ignores it)."""
    value = email.strip()
    if (
        "\x00" in value
        or len(value) > EMAIL_MAX_LENGTH
        # The stored ``email_lower`` column has the same bound.
        or len(value.lower()) > EMAIL_MAX_LENGTH
        or not _EMAIL_PATTERN.fullmatch(value)
    ):
        raise InvalidEmailError("email is not valid")
    return value
