"""Bounded account-owner proof fields shared by request schemas."""

from typing import Annotated

from pydantic import StringConstraints

Password = Annotated[str, StringConstraints(min_length=1, max_length=256)]
# A TOTP code or a recovery code as typed: six digits or ten letters, with spaces or hyphens.
TotpCode = Annotated[str, StringConstraints(min_length=1, max_length=64)]
