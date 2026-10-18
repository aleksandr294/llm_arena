import pytest

from components.users.exceptions import InvalidEmailError
from components.users.utils import normalize_email


@pytest.mark.parametrize(
    "email",
    [
        "a\x00@example.com",
        "a@exam\x00ple.com",
        "İ@example.com",
        "café@example.com",
        "a@example.cöm",
        "a b@example.com",
        "a" * 250 + "@e.com",
    ],
)
def test_normalize_email_rejects(email: str) -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email(email)


def test_normalize_email_accepts_ascii_and_max_length() -> None:
    assert normalize_email("  Ann+tag@Example.com ") == "Ann+tag@Example.com"
    longest = "a" * (254 - len("@example.com")) + "@example.com"
    assert normalize_email(longest) == longest
