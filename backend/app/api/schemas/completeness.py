"""Shape of `GET /projects/{project_id}/telemetry-completeness`.

Loss counts are summed per source and reason over many hours of `bigint`s, so a total can pass
2^53, beyond which a JSON number read by a browser is rounded. Every count here is clamped to
`MAX_JSON_COUNT`, the largest integer such a reader holds exactly; the ratio is computed from the
true totals before clamping.
"""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

MAX_JSON_COUNT = 2**53 - 1


def _clamp(count: int) -> int:
    return min(count, MAX_JSON_COUNT)


JsonCount = Annotated[int, AfterValidator(_clamp)]


class LossOut(BaseModel):
    source: str
    reason: str
    count: JsonCount = Field(description=f"Clamped to {MAX_JSON_COUNT} (2^53 - 1).")


class CompletenessOut(BaseModel):
    stored_spans: JsonCount = Field(description=f"Clamped to {MAX_JSON_COUNT} (2^53 - 1).")
    losses: list[LossOut]
    completeness: str | None = Field(
        description=(
            "stored / (stored + every loss), to four places rounded down; null when nothing was "
            "stored and nothing lost, `0` when only losses were counted."
        )
    )
    lower_bound: bool = Field(
        description=(
            "Both SDK `failed` and ingest rejections were counted: a span the server rejected may "
            "be in both, so the ratio may be lower than the truth."
        )
    )
