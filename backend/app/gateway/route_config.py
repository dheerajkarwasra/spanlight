"""A gateway route's configuration: its targets, retry and fallback policies and time budget.

Pure: no database, no I/O. Stored as JSON in `gateway_routes.config` and in each row of
`gateway_route_versions`. Validated with these models when it is saved: unknown fields are
rejected everywhere, so a misspelt key is a `422`, never a setting that silently does nothing,
and integers are strict: `true` or `2.0` is not a count. A stored config is read back with
`load_stored`, which trusts what was validated on the way in and does not apply today's bounds
again, so a rule tightened later never makes an existing route unreadable.

What these models cannot check is that each target's credential belongs to the route's
organization; the route service does that against the database.
"""

import enum
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

MAX_TARGETS = 8
MAX_MODEL_ALIASES = 32
MAX_RETRY_STATUSES = 16
DEFAULT_RETRY_STATUSES = (408, 409, 429, 500, 502, 503, 504)

ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:/-]{1,128}$")]
"""A model name as a client requests it or a provider knows it, such as `claude-sonnet-4-5`."""


class FallbackCondition(enum.StrEnum):
    """A failure after which the gateway moves on to the route's next target."""

    STATUS_5XX = "status_5xx"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    # A `401` or `403`. Opt-in only: another credential quietly answering for a rejected key
    # would hide a revoked key and move its spend elsewhere.
    AUTH_ERROR = "auth_error"


DEFAULT_FALLBACK_CONDITIONS = (
    FallbackCondition.STATUS_5XX,
    FallbackCondition.RATE_LIMITED,
    FallbackCondition.TIMEOUT,
    FallbackCondition.CONNECTION_ERROR,
)
"""Every condition but `auth_error`, which a route has to list itself."""


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Target(_ConfigModel):
    """One provider credential the route can send a call to."""

    credential_id: uuid.UUID
    # Only the first pick is weighted; fallbacks follow the order of `targets`.
    weight: Annotated[int, Field(strict=True, ge=1, le=100)] = 1
    # A requested model without an alias is sent unchanged.
    model_aliases: Annotated[dict[ModelName, ModelName], Field(max_length=MAX_MODEL_ALIASES)] = (
        Field(default_factory=dict)
    )


class RetryPolicy(_ConfigModel):
    """How often a target is tried, the first try included, and how long to wait in between.

    The wait before retry *n* is `min(backoff_ms * 2 ** (n - 1), max_backoff_ms)`, replaced by a
    provider's `Retry-After` when `honour_retry_after` is set.
    """

    max_attempts: Annotated[int, Field(strict=True, ge=1, le=5)] = 2
    on_statuses: Annotated[
        list[Annotated[int, Field(strict=True, ge=400, le=599)]],
        Field(max_length=MAX_RETRY_STATUSES),
    ] = Field(default_factory=lambda: list(DEFAULT_RETRY_STATUSES))
    backoff_ms: Annotated[int, Field(strict=True, ge=0, le=10_000)] = 200
    # `validate_default`: the floor of `backoff_ms` applies to the default as well, so
    # `{"backoff_ms": 5000}` alone is refused rather than capped below its own backoff.
    max_backoff_ms: Annotated[int, Field(strict=True, ge=0, le=30_000, validate_default=True)] = (
        2000
    )
    honour_retry_after: Annotated[bool, Field(strict=True)] = True

    @field_validator("on_statuses")
    @classmethod
    def _unique_statuses(cls, statuses: list[int]) -> list[int]:
        return _unique(statuses)

    @field_validator("max_backoff_ms")
    @classmethod
    def _not_below_backoff(cls, max_backoff_ms: int, info: ValidationInfo) -> int:
        # `backoff_ms` is absent from `info.data` when it failed validation itself; its own error
        # is then the one to report.
        backoff_ms = info.data.get("backoff_ms")
        if backoff_ms is not None and max_backoff_ms < backoff_ms:
            raise ValueError("must be at least backoff_ms")
        return max_backoff_ms


class FallbackPolicy(_ConfigModel):
    """The failures after which the gateway tries the next target. Empty means never."""

    on: list[FallbackCondition] = Field(default_factory=lambda: list(DEFAULT_FALLBACK_CONDITIONS))

    @field_validator("on")
    @classmethod
    def _unique_conditions(cls, conditions: list[FallbackCondition]) -> list[FallbackCondition]:
        return _unique(conditions)


class RouteConfig(_ConfigModel):
    """Which credentials serve the route's calls, in fallback order, and how failures are handled.

    `timeout_ms` is the whole budget for every attempt, wait and fallback of one call; for a
    stream it bounds the time to the first byte.
    """

    targets: Annotated[list[Target], Field(min_length=1, max_length=MAX_TARGETS)]
    retry: RetryPolicy
    fallback: FallbackPolicy
    timeout_ms: Annotated[int, Field(strict=True, ge=1000, le=600_000)] = 60_000

    @model_validator(mode="after")
    def _auth_error_needs_another_credential(self) -> "RouteConfig":
        # Falling back to the same key after it was refused would only be refused again.
        if FallbackCondition.AUTH_ERROR in self.fallback.on and len(self.credential_ids()) < 2:
            raise PydanticCustomError(
                "auth_error_fallback",
                "fallback.on: auth_error needs two targets on different credentials",
            )
        return self

    def credential_ids(self) -> list[uuid.UUID]:
        """The distinct credentials the targets use, in the order they first appear.

        A credential may serve several targets (for example two models on the same key).
        """
        return list(dict.fromkeys(target.credential_id for target in self.targets))


def load_stored(stored: Mapping[str, Any]) -> RouteConfig:
    """A config read back from the database, built without validating it again.

    Every stored config passed `RouteConfig` when it was saved, so its shape is trusted; only
    the bounds may have tightened since. Ids and enum values are rebuilt from their JSON form
    so the result serializes like a validated config. Saving a config again (a revert) goes
    through `RouteConfig.model_validate` instead and meets today's rules.
    """
    return RouteConfig.model_construct(
        targets=[
            Target.model_construct(
                **{**target, "credential_id": uuid.UUID(target["credential_id"])}
            )
            for target in stored["targets"]
        ],
        retry=RetryPolicy.model_construct(**stored["retry"]),
        fallback=FallbackPolicy.model_construct(
            on=[FallbackCondition(condition) for condition in stored["fallback"]["on"]]
        ),
        timeout_ms=stored["timeout_ms"],
    )


def _unique[T](values: Sequence[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("must not repeat a value")
    return list(values)
