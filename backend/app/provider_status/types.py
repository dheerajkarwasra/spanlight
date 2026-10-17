"""Shapes other domains read the provider incidents through. Pure: no I/O."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

DETECTOR_GRACE = timedelta(minutes=15)
"""How long after it resolves an incident still overlaps a detector's window: feeds
often resolve before the last errors drain. The reader and the detector apply the same one."""


class TimeRange(Protocol):
    """A half-open `[start, end)` window, such as the API's `TimeWindow`."""

    @property
    def start(self) -> datetime: ...

    @property
    def end(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ProviderIncidentRow:
    """The fields a detector cites an incident by."""

    id: uuid.UUID
    provider: str
    title: str
    url: str
    impact: str
    started_at: datetime
    resolved_at: datetime | None
