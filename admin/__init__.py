"""Optional admin panel (fastadmin) mounted at ``/admin``.

fastadmin reads its configuration from environment variables once, when it
is first imported. Nothing in the main application may therefore import
``fastadmin`` (or any ``admin`` submodule that does, such as ``admin.core``)
at module level: ``mount_admin`` exports the settings to the environment
first and only then imports it, exactly once per process. When the panel is
not configured it is never imported at all.
"""

import importlib
import logging
import os
import sys

from fastapi import FastAPI

from components.core.config import SettingsSchema

logger = logging.getLogger(__name__)

ADMIN_PREFIX = "admin"

# Modules that register model admins with ``fastadmin.register``. They are
# imported by ``mount_admin`` after the environment is set; each per-model
# admin module adds its dotted name here.
ADMIN_MODULES: list[str] = [
    "admin.users.admin",
    "admin.organizations.admin",
    "admin.datasets.admin",
    "admin.experiments.admin",
    "admin.evaluation.admin",
]


def _export_environment(settings: SettingsSchema) -> str:
    """Set the variables fastadmin reads at import time; return the secret."""
    assert settings.admin_secret_key is not None
    secret = settings.admin_secret_key.get_secret_value()
    os.environ["ADMIN_SECRET_KEY"] = secret
    os.environ["ADMIN_USER_MODEL"] = "User"
    os.environ["ADMIN_USER_MODEL_USERNAME_FIELD"] = "email"
    os.environ["ADMIN_PREFIX"] = ADMIN_PREFIX
    os.environ["ADMIN_SESSION_COOKIE_SECURE"] = (
        "true" if settings.admin_session_cookie_secure else "false"
    )
    os.environ["ADMIN_SESSION_EXPIRED_AT"] = str(
        settings.admin_session_ttl_seconds
    )
    # Lax keeps the cookie off cross-site POST/PATCH/DELETE requests (CSRF).
    # Pinned so an ambient environment variable cannot weaken it.
    os.environ["ADMIN_SESSION_COOKIE_SAMESITE"] = "lax"
    return secret


def mount_admin(app: FastAPI, settings: SettingsSchema) -> bool:
    """Mount the admin panel under ``/admin``. Returns whether it was
    mounted; without ``admin_secret_key`` nothing happens and fastadmin is
    not imported."""
    if settings.admin_secret_key is None:
        logger.info("Admin panel disabled: ADMIN_SECRET_KEY is not set")
        return False

    # The environment is read once at import, so a copy imported earlier in
    # this process (for example by a test) would silently ignore our
    # settings and could sign sessions with the wrong key. Check this before
    # exporting anything so a failed mount leaves the environment untouched.
    # The error message never contains the secret.
    imported = sys.modules.get("fastadmin.settings")
    if (
        imported is not None
        and imported.settings.ADMIN_SECRET_KEY
        != settings.admin_secret_key.get_secret_value()
    ):
        raise RuntimeError(
            "fastadmin was imported before mount_admin configured it; "
            "it must only be imported through mount_admin"
        )
    _export_environment(settings)
    from fastadmin import fastapi_app

    for module in ADMIN_MODULES:
        importlib.import_module(module)
    app.mount(f"/{ADMIN_PREFIX}", fastapi_app)
    logger.info("Admin panel mounted at /%s", ADMIN_PREFIX)
    return True
