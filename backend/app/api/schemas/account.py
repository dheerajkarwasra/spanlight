"""The caller's own account: terms acceptance, the profile, the email address and deletion."""

from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints

from app.api.schemas.auth import LinkToken, TotpCode
from app.api.schemas.common import Confirmation, Name


class TermsOut(BaseModel):
    """The terms version this server asks for; null when it asks for none."""

    version: str | None


class TermsAcceptIn(BaseModel):
    """The version the caller read; it must be the server's current one."""

    version: str


class ProfileIn(BaseModel):
    """The fields to change; a field left out stays as it is."""

    name: Name | None = None
    # False turns the weekly digest off for the caller in every project; true turns it back on.
    weekly_digest: bool | None = None


Password = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class EmailChangeIn(BaseModel):
    new_email: EmailStr
    # Required when the account has a password; an account without one re-signs in instead.
    password: Password | None = None
    # Required when two-factor authentication is on: a TOTP code or an unused recovery code.
    code: TotpCode | None = None


class EmailChangeConfirmIn(BaseModel):
    token: LinkToken


class AccountDeleteIn(BaseModel):
    # Required when the account has a password; an account without one re-signs in instead.
    password: Password | None = None
    # Required when two-factor authentication is on: a TOTP code or an unused recovery code.
    code: TotpCode | None = None
    # The word `delete`, typed out; compared as typed, like an org's slug (`Confirmation`).
    confirm: Confirmation
