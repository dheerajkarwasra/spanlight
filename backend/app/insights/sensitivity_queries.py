"""The SQL behind Doctor sensitivity (`app.insights.sensitivity`).

`detector_sensitivity` is under row-level security: callers run in a transaction bound to the
project; the explicit `project_id` filter is a second guard.
"""

import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetectorSensitivity
from app.insights.sensitivity import Level


async def load_levels(db: AsyncSession, project_id: uuid.UUID) -> dict[str, Level]:
    """The project's setting per check; a check that is absent is at High recall."""
    rows = await db.execute(
        select(DetectorSensitivity.detector, DetectorSensitivity.level).where(
            DetectorSensitivity.project_id == project_id
        )
    )
    # The migration's CHECK holds `level` to the three values of `Level`.
    return {detector: cast(Level, level) for detector, level in rows}
