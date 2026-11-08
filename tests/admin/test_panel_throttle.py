"""Panel sign-in is throttled per username and leaves a security trail."""

from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import Panel

WRONG_PASSWORD = "SENTINEL-WRONG-PASSWORD-3c9e"


def test_lockout_blocks_the_correct_password_and_events_are_logged(
    world: World, emails: dict[str, str]
) -> None:
    panel = Panel(debug_logs=True, extra_env={"AUTH_MAX_FAILED_ATTEMPTS": "2"})
    email = emails["staff_a"]
    try:
        for _ in range(2):
            assert panel.sign_in("a", email, WRONG_PASSWORD).status == 401
        locked = panel.sign_in("a", email, PASSWORD)
        other = panel.sign_in("b", emails["staff_b"], PASSWORD)
    finally:
        logs = panel.close()

    assert locked.status == 401  # the same answer as any other failure
    assert other.status == 200  # another account is unaffected
    # The child logs plain "LEVEL:logger:message" lines.
    assert logs.count("security:admin.sign_in") == 3
    assert "security:auth.locked_out" in logs
    for secret in (email, WRONG_PASSWORD, PASSWORD):
        assert secret not in logs
