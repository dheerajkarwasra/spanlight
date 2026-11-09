"""Project API keys."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.api.schemas.common import FutureTime, Name, Password, TotpCode, UserOut
from app.core.scopes import DEFAULT_KEY_SCOPES, KeyScope


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[KeyScope]
    created_by: UserOut | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    secret: str


class ApiKeyCreateIn(BaseModel):
    name: Name
    # Ingest-only by default, which is all a key could do before scopes existed. Pydantic copies
    # a mutable default for each instance.
    scopes: list[KeyScope] = Field(default=list(DEFAULT_KEY_SCOPES), min_length=1)
    # None: the key never expires.
    expires_at: FutureTime | None = None
    password: Password | None = None
    code: TotpCode | None = None

    @field_validator("scopes")
    @classmethod
    def _deduplicate(cls, scopes: list[KeyScope]) -> list[KeyScope]:
        return list(dict.fromkeys(scopes))
