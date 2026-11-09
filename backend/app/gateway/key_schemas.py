"""What a gateway key is created and edited from. Pure: no database, no I/O.

The bounds match the database CHECKs of migration 0202 and the trace header rules: a key's
`default_tags` are added to every trace it sends, so they follow the bounds of
`x-spanlight-tags` (at most 20 tags of at most 64 characters each). Integers are strict, so
`true` or `2.0` is not a limit. Unknown fields are rejected.
"""

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.auth.proof_types import Password, TotpCode
from app.db.models import GatewayKey
from app.gateway.route_config import ModelName

MAX_ALLOWED_MODELS = 100
MAX_DEFAULT_TAGS = 20

KeyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Environment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
"""Free text such as `production` or `staging`. Fault profiles never run on `production`."""
PRODUCTION_ENVIRONMENT = "production"
"""The canonical value fault profiles refuse; compare with `is_production_environment`."""


def is_production_environment(environment: str) -> bool:
    """Whether `environment` names production: ignoring case and surrounding whitespace.

    The environment is free text, so `Production` and `PRODUCTION` count. This is the one test
    for the fault profile rule, at attach time and at call time.
    """
    return environment.strip().casefold() == PRODUCTION_ENVIRONMENT


Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
RpmLimit = Annotated[int, Field(strict=True, ge=1, le=100_000)]
TpmLimit = Annotated[int, Field(strict=True, ge=1, le=100_000_000)]
CacheTtl = Annotated[int, Field(strict=True, ge=1, le=86_400)]
AllowedModels = Annotated[list[ModelName], Field(max_length=MAX_ALLOWED_MODELS)]
"""The model names a key may request, before alias resolution. Empty allows any model."""
DefaultTags = Annotated[list[Tag], Field(max_length=MAX_DEFAULT_TAGS)]


def _unique[T](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("must not repeat a value")
    return values


class GatewayKeyCreate(BaseModel):
    """A new gateway key. Without `route_id` it uses the project's default route.

    A `None` limit means no limit; a `None` `cache_ttl_seconds` leaves the cache off.
    """

    model_config = ConfigDict(extra="forbid")

    password: Password | None = None
    code: TotpCode | None = None

    name: KeyName
    route_id: uuid.UUID | None = None
    environment: Environment
    rpm_limit: RpmLimit | None = None
    tpm_limit: TpmLimit | None = None
    allowed_models: AllowedModels = Field(default_factory=list)
    default_tags: DefaultTags = Field(default_factory=list)
    cache_ttl_seconds: CacheTtl | None = None

    @field_validator("allowed_models", "default_tags")
    @classmethod
    def _no_repeats(cls, values: list[str]) -> list[str]:
        return _unique(values)


class GatewayKeyUpdate(BaseModel):
    """A partial edit: only the fields sent change.

    The four nullable settings (`rpm_limit`, `tpm_limit`, `cache_ttl_seconds`, `fault_profile_id`)
    are cleared by sending `null`; every other field must have a value when it is sent.
    `changes()` tells an absent field from one sent as `null`.
    """

    model_config = ConfigDict(extra="forbid")

    password: Password | None = None
    code: TotpCode | None = None

    name: KeyName | None = None
    route_id: uuid.UUID | None = None
    environment: Environment | None = None
    rpm_limit: RpmLimit | None = None
    tpm_limit: TpmLimit | None = None
    allowed_models: AllowedModels | None = None
    default_tags: DefaultTags | None = None
    cache_ttl_seconds: CacheTtl | None = None
    fault_profile_id: uuid.UUID | None = None

    @field_validator("name", "route_id", "environment", "allowed_models", "default_tags")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        # Runs only for a value that was sent: an absent field keeps its default untouched.
        if value is None:
            raise ValueError("must not be null")
        return value

    @field_validator("allowed_models", "default_tags")
    @classmethod
    def _no_repeats(cls, values: list[str]) -> list[str]:
        return _unique(values)

    def changes(self) -> dict[str, Any]:
        """The fields that were sent, with their values."""
        return {name: getattr(self, name) for name in self.model_fields_set - {"password", "code"}}


def widens(key: GatewayKey, changes: dict[str, Any]) -> bool:
    """Whether spending authority grows; a fault profile only makes a key fail or slow."""
    for field in ("route_id", "environment"):
        if field in changes and changes[field] != getattr(key, field):
            return True
    for field in ("rpm_limit", "tpm_limit"):
        previous = getattr(key, field)
        if field in changes and previous is not None:
            proposed = changes[field]
            if proposed is None or proposed > previous:
                return True
    models = changes.get("allowed_models")
    if models is not None and key.allowed_models:
        return not models or bool(set(models) - set(key.allowed_models))
    return False
