"""Sign-in providers and confirmation before connecting one to an account."""

from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.account import Password, TotpCode
from app.api.schemas.common import ApiModel
from app.config import OAuthProviderName


class OAuthProviderOut(BaseModel):
    provider: OAuthProviderName


class OAuthIdentityOut(ApiModel):
    """A sign-in provider account linked to the caller."""

    provider: OAuthProviderName
    email: str | None
    created_at: datetime
    last_used_at: datetime


class OAuthLinkIn(BaseModel):
    password: Password | None = None
    code: TotpCode | None = None
    next: str | None = None


class OAuthLinkOut(BaseModel):
    url: str
