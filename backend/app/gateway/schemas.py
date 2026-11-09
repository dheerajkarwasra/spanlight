"""Inputs and results the gateway domain owns: what a new provider credential is made from."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from app.auth.proof_types import Password, TotpCode
from app.db.models import ProviderKind

CredentialName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


MAX_API_KEY_LENGTH = 512


def _check_api_key(value: SecretStr) -> SecretStr:
    """Trim the key and require visible ASCII. No message ever repeats the value.

    A key pasted from a terminal often carries a trailing newline, which the provider would
    reject as a wrong key. Provider keys are visible ASCII; anything else could not be sent in an
    HTTP header at all, so it is refused here as a `422` rather than failing on first use.
    """
    stripped = value.get_secret_value().strip()
    if not stripped:
        raise ValueError("must not be empty")
    if len(stripped) > MAX_API_KEY_LENGTH:
        raise ValueError(f"must be at most {MAX_API_KEY_LENGTH} characters")
    if not all("!" <= character <= "~" for character in stripped):
        raise ValueError("must contain only visible ASCII characters, without spaces")
    return SecretStr(stripped)


ProviderApiKey = Annotated[
    SecretStr, Field(max_length=4 * MAX_API_KEY_LENGTH), AfterValidator(_check_api_key)
]
"""A provider API key as sent by the client. A `SecretStr`, so a repr or a log line masks it.

The `Field` bound only stops an oversized body early; `_check_api_key` applies the real limit
after trimming.
"""


class CredentialCreate(BaseModel):
    """A new provider credential. `base_url` is only for `openai_compatible`, which needs it."""

    model_config = ConfigDict(extra="forbid")

    password: Password | None = None
    code: TotpCode | None = None

    name: CredentialName
    provider: ProviderKind
    api_key: ProviderApiKey
    base_url: Annotated[str, StringConstraints(max_length=2048)] | None = None


CheckStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class CredentialCheck:
    """The outcome of calling the provider's model list with the credential."""

    status: CheckStatus
    checked_at: datetime
    # A short status line such as "401 Unauthorized"; None when the check passed.
    error: str | None
