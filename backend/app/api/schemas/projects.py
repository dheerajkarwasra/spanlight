"""Projects and their onboarding state."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.alerts.rule_spec import ChannelIds
from app.api.schemas.common import ApiModel, Confirmation, Name
from app.ingest.metadata import MetadataKey

MAX_PROMOTED_KEYS = 5


def _promoted(keys: list[str]) -> list[str]:
    """Without repeats, in the order given; at most five remain."""
    unique = list(dict.fromkeys(keys))
    if len(unique) > MAX_PROMOTED_KEYS:
        raise ValueError(f"at most {MAX_PROMOTED_KEYS} promoted keys")
    return unique


PromotedKeys = Annotated[list[MetadataKey], AfterValidator(_promoted)]


class ProjectOut(ApiModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    slug: str
    retention_days: int
    capture_payloads: bool
    weekly_digest_enabled: bool
    # Alert channels of the organization that hear about critical insights.
    insight_channel_ids: list[uuid.UUID]
    # Metadata keys the daily attribution rollup keeps, in the order they were set.
    promoted_metadata_keys: list[str]
    created_at: datetime


class ProjectCreateIn(BaseModel):
    name: Name


class ProjectUpdateIn(BaseModel):
    name: Name | None = None
    retention_days: Annotated[int, Field(ge=1, le=90)] | None = None
    capture_payloads: bool | None = None
    weekly_digest_enabled: bool | None = None
    # At most 10, repeats dropped; each must be an alert channel of the project's organization
    # (else 422 `UNKNOWN_CHANNEL`). `[]` turns insight notifications off.
    insight_channel_ids: ChannelIds | None = None
    # At most 5 after repeats are dropped, order kept; each matches the metadata key pattern.
    # `[]` promotes none. The attribution rollup backfills a new key over the last 7 UTC days.
    promoted_metadata_keys: PromotedKeys | None = None


class ProjectDeleteIn(BaseModel):
    # The project's slug, typed out as it is shown. The route compares it; see `Confirmation`.
    confirm: Confirmation


class OnboardingOut(BaseModel):
    has_traces: bool
    first_trace_at: datetime | None


class ActivationOut(BaseModel):
    """Which setup checklist steps the project has done, each read from its own rows."""

    # A trace other than the one "Send a test trace" sends (tagged `spanlight:setup-test`).
    first_trace: bool
    # A trace with a `session_id`.
    session: bool
    # A budget of any scope.
    budget: bool
    # A score from an evaluator or a person; scores sent with an API key do not count.
    evaluation: bool
