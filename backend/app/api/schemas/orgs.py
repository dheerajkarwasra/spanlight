"""Organizations, members, invites and the audit log."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.api.schemas.common import ApiModel, Confirmation, Name, UserOut
from app.db.models import MembershipRole


class OrgOut(ApiModel):
    id: uuid.UUID
    name: str
    slug: str
    is_demo: bool
    # Whether members must have two-factor authentication to use the org.
    require_2fa: bool


class OrgWithRoleOut(OrgOut):
    role: MembershipRole


class OrgCreateIn(BaseModel):
    name: Name


class OrgUpdateIn(BaseModel):
    """A change to the organization: any of its fields, and at least one.

    A field that is left out is left alone; `null` is not a way to say that, so it is refused.
    Strict about unknown fields, so a client that sends one this route does not handle is told
    instead of being answered 200 with nothing changed.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    require_2fa: bool | None = None

    @field_validator("name", "require_2fa")
    @classmethod
    def _reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("may be left out but not null")
        return value

    @model_validator(mode="after")
    def _require_a_change(self) -> "OrgUpdateIn":
        if not self.model_fields_set:
            raise ValueError("send at least one of: name, require_2fa")
        return self


class OrgDeleteIn(BaseModel):
    # The org's slug, typed out as it is shown. The route compares it; see `Confirmation`.
    confirm: Confirmation


class MemberOut(BaseModel):
    user: UserOut
    role: MembershipRole
    created_at: datetime


class MemberUpdateIn(BaseModel):
    role: MembershipRole


class InviteCreateIn(BaseModel):
    role: MembershipRole
    # Where to mail the link. Without it, the caller shares the returned `url` by hand.
    email: EmailStr | None = None


class InviteCreatedOut(BaseModel):
    id: uuid.UUID
    role: MembershipRole
    email: str | None
    url: str
    expires_at: datetime


class InviteOut(ApiModel):
    id: uuid.UUID
    role: MembershipRole
    email: str | None
    created_at: datetime
    expires_at: datetime


class InviteAcceptIn(BaseModel):
    token: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class InviteOrgOut(ApiModel):
    id: uuid.UUID
    name: str
    slug: str


class InvitePreviewOut(BaseModel):
    """What an invite grants, shown to the signed-in invitee before they accept."""

    org: InviteOrgOut
    role: MembershipRole
    expires_at: datetime


class AuditEventOut(BaseModel):
    id: uuid.UUID
    action: str
    actor: UserOut | None
    # `user`, `token`, `system` or `cli`; null on events written before it was recorded.
    actor_type: str | None
    # The actor's email when the event was written: still there after the user is gone.
    actor_email: str | None
    credential_id: uuid.UUID | None
    project_id: uuid.UUID | None
    target_type: str
    target_id: str
    outcome: Literal["success", "failure"]
    reason: str | None
    metadata: dict[str, Any]
    ip: str | None
    user_agent: str | None
    request_id: str | None
    created_at: datetime
    # The org's hash chain (hex SHA-256): `hash` covers the event and `prev_hash`, so each event
    # vouches for the one before it. Null on an event the chain does not cover.
    seq: int | None
    prev_hash: str | None
    hash: str | None


class AuditIntegrityOut(BaseModel):
    """`GET /orgs/{id}/audit/integrity`: the org's hash chain, recomputed from the stored events."""

    ok: bool
    checked: int
    first_seq: int | None
    head_seq: int | None
    # Keep it somewhere else: a later check that no longer reaches it means events were removed.
    head_hash: str | None
    first_broken_seq: int | None


class AuditStreamIn(BaseModel):
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    enabled: bool = True


class AuditStreamOut(BaseModel):
    url: str
    enabled: bool
    # The last event the endpoint accepted, and the newest event: their difference is pending.
    delivered_seq: int
    head_seq: int
    last_delivered_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime


class AuditStreamSavedOut(BaseModel):
    stream: AuditStreamOut
    # The signing secret, in this response only: when the stream is created or the secret rotated.
    secret: str | None


class AuditStreamTestOut(BaseModel):
    ok: bool
    error: str | None
