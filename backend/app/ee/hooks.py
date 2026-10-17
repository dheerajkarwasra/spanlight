"""Where the enterprise edition plugs into the processes.

`app.main` mounts `routers()` and stores `load(...)` on `app.state.ee_license`; the worker merges
`tasks()` and calls `load(...)` for its log line. An ee feature adds its routers and tasks here
and checks its license with `app.ee.gate`.
"""

from datetime import datetime

import structlog
from fastapi import APIRouter

from app.config import Settings
from app.ee.license import License, load_license
from app.jobs.context import TaskHandler

logger = structlog.get_logger(__name__)

# How long before expiry startup starts warning.
EXPIRY_WARNING_DAYS = 30


def routers() -> list[APIRouter]:
    """The ee routers. Always mounted, licensed or not: each route checks with `require_ee`."""
    return []


def tasks() -> dict[str, TaskHandler]:
    """The ee job handlers, by kind. Each checks `license_allows` and ends `skipped` without it."""
    return {}


def load(settings: Settings, now: datetime) -> License | None:
    """Load the license for a process and log the edition it runs as.

    Logs the license's id, licensee, signing key, expiry and features, never the key itself, and
    warns when it is expired or expires within `EXPIRY_WARNING_DAYS`.
    """
    ee_license = load_license(settings)
    if ee_license is None:
        logger.info("edition", edition="community")
        return None
    fields = {
        "license_id": ee_license.license_id,
        "licensee": ee_license.licensee,
        "kid": ee_license.kid,
        "expires_at": ee_license.expires_at.isoformat(),
        "features": sorted(ee_license.features),
    }
    logger.info("edition", edition="enterprise", **fields)
    days_left = (ee_license.expires_at - now).days
    if ee_license.is_expired(now):
        logger.warning("ee_license_expired", **fields)
    elif days_left < EXPIRY_WARNING_DAYS:
        logger.warning("ee_license_expires_soon", days_left=days_left, **fields)
    return ee_license
