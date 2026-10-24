"""Sign-up, login, the current user, their sessions and their linked sign-in providers.

`MembershipOut` embeds `OrgOut`, so this module imports from `orgs`; `orgs` never imports from
here.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints

from app.api.schemas.common import ApiModel, Name, UserOut
from app.api.schemas.orgs import OrgOut
from app.config import OAuthProviderName
from app.db.models import MembershipRole


class MembershipOut(BaseModel):
    org: OrgOut
    role: MembershipRole


class CapabilitiesOut(BaseModel):
    """What this server is set up to do, so the dashboard can explain a missing feature up front.

    Booleans and the gateway mode only: never a key, key id, bucket or other configured value.
    """

    gateway_mode: Literal["embedded", "standalone", "disabled"]
    # CREDENTIALS_KEYS is set, so provider credentials can be saved.
    credentials_configured: bool
    # Object storage (bucket and both keys) is set, so exports have somewhere to go.
    exports_configured: bool


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    # Whether a password can sign this user in. False for someone who signed up with GitHub or
    # Google and never set one. A flag about the caller's own account, so it is not on UserOut,
    # which other people's profiles are shown through.
    has_password: bool
    # Whether two-factor authentication is on for the caller. Self-only, like `has_password`:
    # the org's `require_2fa` says what an org needs, this says whether the caller has it.
    totp_enabled: bool
    # Whether the caller has to verify their email address: the server can send email, the
    # address is not yet verified and the caller is not the shared demo account, which is never
    # asked and cannot receive email. False on a server without email, where no link can arrive,
    # so the dashboard shows no verification prompt there. Self-only, like `has_password`.
    email_verification_required: bool
    capabilities: CapabilitiesOut


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


# A password being chosen (signup, reset). Login accepts any non-empty password instead, so a
# change to these limits never locks out an account that already exists.
NewPassword = Annotated[str, StringConstraints(min_length=10, max_length=256)]
# The secret from an emailed link, as the page read it from the URL fragment.
LinkToken = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class SignupIn(BaseModel):
    email: EmailStr
    password: NewPassword
    name: Name


class LoginIn(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class LoginSignedInOut(BaseModel):
    """The password (or the second step) was enough: the session cookies are set."""

    status: Literal["signed_in"]
    user: UserOut


class LoginTotpRequiredOut(BaseModel):
    """The password was right but the account has two-factor authentication: no cookies yet.

    Send `challenge` with a code to `POST /auth/totp/verify` before `expires_at`.
    """

    status: Literal["totp_required"]
    challenge: str
    expires_at: datetime


LoginOut = Annotated[LoginSignedInOut | LoginTotpRequiredOut, Field(discriminator="status")]

# A TOTP code or a recovery code as typed: six digits or ten letters, with spaces or hyphens.
TotpCode = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class TotpCodeIn(BaseModel):
    code: TotpCode


class TotpSetupIn(BaseModel):
    # Required when the account has a password; an account without one re-signs in instead.
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None


class TotpVerifyIn(BaseModel):
    # No minimum: an empty challenge is as invalid as a forged one and gets the same answer.
    challenge: Annotated[str, StringConstraints(max_length=1024)]
    code: TotpCode


class TotpStatusOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None
    recovery_codes_remaining: int


class TotpSetupOut(BaseModel):
    """What to add to an authenticator app. Returned once; the secret is never shown again."""

    secret: str
    otpauth_url: str


class TotpEnabledOut(BaseModel):
    """The recovery codes, in clear text this once: the server keeps only their hashes."""

    recovery_codes: list[str]


class AcceptedOut(BaseModel):
    """The answer to a request that is carried out later, by the worker."""

    status: Literal["accepted"]


class EmailVerifyConfirmIn(BaseModel):
    token: LinkToken


class PasswordForgotIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: LinkToken
    password: NewPassword


class OAuthProviderOut(BaseModel):
    provider: OAuthProviderName


class OAuthIdentityOut(ApiModel):
    """A sign-in provider account linked to the caller."""

    provider: OAuthProviderName
    email: str | None
    created_at: datetime
    last_used_at: datetime
