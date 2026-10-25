"""Doctor sensitivity: one item per check, the preview of each setting, and the setting body.

Gates are decimal strings, like every other stored threshold; `metric_label` and `gates` are null
for a fixed check (one with a single setting).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.insights.sensitivity import Level


class GatesOut(BaseModel):
    balanced: str
    high_precision: str


class SensitivityOut(BaseModel):
    detector: str
    tunable: bool
    level: Level
    metric_label: str | None
    gates: GatesOut | None
    findings_30d: int
    updated_at: datetime | None


class SensitivityListOut(BaseModel):
    items: list[SensitivityOut]


class WindowCountsOut(BaseModel):
    days: int
    high_recall: int
    balanced: int
    high_precision: int


class SensitivityPreviewOut(BaseModel):
    detector: str
    windows: list[WindowCountsOut]


class SensitivityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Level
