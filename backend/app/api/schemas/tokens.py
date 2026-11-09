"""Personal access tokens."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.account import Password, TotpCode
from app.api.schemas.common import ApiModel, FutureTime, Name
from app.db.models import TokenScope


class PersonalAccessTokenOut(ApiModel):
    id: uuid.UUID
    name: str
    prefix: str
    scope: TokenScope
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


class PersonalAccessTokenCreatedOut(PersonalAccessTokenOut):
    # The whole token, shown here and nowhere else: only a digest of its secret is stored.
    token: str


class PersonalAccessTokenCreateIn(BaseModel):
    name: Name
    # Required, with no default: how much a token may do is a decision to make each time.
    scope: TokenScope
    # None: the token never expires.
    expires_at: FutureTime | None = None
    password: Password | None = None
    code: TotpCode | None = None
