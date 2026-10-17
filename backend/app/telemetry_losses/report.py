"""The telemetry completeness answer for one project and window.

`stored_spans` counts the spans that started in the window exactly (`app.telemetry_losses.stored`).
Losses are kept per wall-clock hour (`app.telemetry_losses.queries.read_losses`), so every hour
that overlaps the window counts whole: a window that starts or ends inside an hour includes that
hour's losses in full. The ratio is `app.telemetry_losses.completeness.ratio` over the true
totals; the schema clamps the counts it sends to what JSON carries exactly.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.completeness import CompletenessOut, LossOut
from app.api.window import TimeWindow
from app.telemetry_losses.completeness import ratio
from app.telemetry_losses.queries import read_losses
from app.telemetry_losses.stored import read_stored_spans


async def completeness_report(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, now: datetime
) -> CompletenessOut:
    stored = await read_stored_spans(db, project_id, window, now)
    losses = await read_losses(db, project_id, window)
    result = ratio(stored, {(loss.source, loss.reason): loss.count for loss in losses})
    return CompletenessOut(
        stored_spans=stored,
        losses=[
            LossOut(source=loss.source, reason=loss.reason, count=loss.count) for loss in losses
        ],
        completeness=None if result.completeness is None else format(result.completeness, "f"),
        lower_bound=result.lower_bound,
    )
