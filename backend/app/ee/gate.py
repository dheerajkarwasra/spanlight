"""The checks an enterprise feature runs before it does anything.

A route declares `Depends(require_ee(EEFeature.X))`; a worker task calls `license_allows(...)`
with its own `now` and ends `skipped` when it is false. ee routers are always mounted, so an
unlicensed instance answers `403 EE_LICENSE_REQUIRED` rather than a 404: the caller is signed in
and the route exists, and 404 is kept for hiding what another tenant has.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import Request

from app.api.deps import ClockDep
from app.core.errors import license_required
from app.ee.features import EEFeature
from app.ee.license import License


def license_allows(ee_license: License | None, feature: EEFeature, now: datetime) -> bool:
    return ee_license is not None and ee_license.allows(feature.value, now)


def require_ee(feature: EEFeature) -> Callable[[Request, ClockDep], Awaitable[None]]:
    """A route dependency that refuses unless the instance's license grants `feature` now."""

    async def dependency(request: Request, clock: ClockDep) -> None:
        if not license_allows(request.app.state.ee_license, feature, clock()):
            raise license_required(feature.value)

    return dependency
