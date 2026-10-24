"""The LLM gateway: provider credentials, gateway routes and gateway keys.

The create body, `CredentialCreate`, is owned by the gateway domain (`app.gateway.schemas`),
which validates it; it is re-exported here so routers keep importing from `app.api.schemas`.
No response model has a field for the API key: once sent, it is never returned.

A route's `config` is the gateway domain's `RouteConfig` (`app.gateway.route_config`), in
requests and responses alike. A gateway key's create and edit bodies are the domain's
`GatewayKeyCreate` and `GatewayKeyUpdate` (`app.gateway.key_schemas`), re-exported the same way;
only the create response carries the key's `secret`, once.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import ApiModel, Money, Name, UserOut
from app.db.models import FaultScenario, ProviderKind
from app.gateway.fault_params import FaultParams
from app.gateway.fault_schemas import FaultProfileCreate, FaultProfileUpdate
from app.gateway.key_schemas import GatewayKeyCreate, GatewayKeyUpdate
from app.gateway.route_config import RouteConfig
from app.gateway.schemas import CheckStatus, CredentialCreate, ProviderApiKey

__all__ = [
    "CredentialCheckOut",
    "CredentialCreate",
    "CredentialOut",
    "CredentialRotateIn",
    "FaultProfileCreate",
    "FaultProfileOut",
    "FaultProfileUpdate",
    "FaultScenarioCountOut",
    "GatewayCacheOut",
    "GatewayKeyCreate",
    "GatewayKeyCreatedOut",
    "GatewayKeyOut",
    "GatewayKeyUpdate",
    "GatewayKeyUsageOut",
    "GatewayOverviewOut",
    "GatewayTargetUsageOut",
    "RouteCreateIn",
    "RouteOut",
    "RouteRevertIn",
    "RouteUpdateIn",
    "RouteVersionOut",
]

VersionNumber = Annotated[int, Field(strict=True, ge=1)]


class CredentialOut(BaseModel):
    id: uuid.UUID
    name: str
    provider: ProviderKind
    base_url: str | None
    created_by: UserOut | None
    created_at: datetime
    rotated_at: datetime | None
    last_used_at: datetime | None
    last_checked_at: datetime | None
    last_error: str | None


class CredentialRotateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: ProviderApiKey


class CredentialCheckOut(ApiModel):
    status: CheckStatus
    checked_at: datetime
    error: str | None


class RouteCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    config: RouteConfig


class RouteUpdateIn(BaseModel):
    """A new config, and the version the client last read, which must still be the current one."""

    model_config = ConfigDict(extra="forbid")

    config: RouteConfig
    expected_version: VersionNumber


class RouteRevertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: VersionNumber


class RouteOut(BaseModel):
    id: uuid.UUID
    name: str
    is_default: bool
    config: RouteConfig
    version: int
    # Who saved the current version, and when; `null` once that person has been removed.
    updated_by: UserOut | None
    updated_at: datetime


class RouteVersionOut(BaseModel):
    version: int
    config: RouteConfig
    updated_by: UserOut | None
    created_at: datetime


class GatewayKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    # `null` only for a revoked key whose route has since been deleted.
    route_id: uuid.UUID | None
    environment: str
    # `null` means no limit.
    rpm_limit: int | None
    tpm_limit: int | None
    # Empty means any model.
    allowed_models: list[str]
    default_tags: list[str]
    # `null` means the cache is off for this key.
    cache_ttl_seconds: int | None
    # The Integration Lab profile this key runs; `null` for none, and always for a production key.
    fault_profile_id: uuid.UUID | None
    created_by: UserOut | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class GatewayKeyCreatedOut(GatewayKeyOut):
    # The full key, shown in this response only.
    secret: str


class FaultProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    scenario: FaultScenario
    # The scenario's parameters, defaults filled in.
    params: FaultParams
    # From 0 to 1.
    probability: float
    enabled: bool
    # `null` means the profile does not expire.
    expires_at: datetime | None
    # Whether the profile can fire now: enabled and not expired.
    active: bool
    # The active (not revoked) keys that run this profile.
    attached_key_ids: list[uuid.UUID]
    created_by: UserOut | None
    created_at: datetime
    updated_at: datetime


class GatewayCacheOut(BaseModel):
    # Requests answered from the cache, and requests that looked in it and found nothing.
    hits: int
    misses: int
    # `hits` over `hits + misses`; `null` when nothing looked in the cache. Calls of keys with
    # the cache off, and streams, never look in it and are not counted.
    hit_rate: float | None


class GatewayTargetUsageOut(BaseModel):
    # `null` when the credential was deleted after the calls.
    credential_id: uuid.UUID | None
    credential_name: str
    provider: ProviderKind | None
    requests: int
    errors: int
    p95_ms: float | None


class GatewayKeyUsageOut(BaseModel):
    key_id: uuid.UUID
    name: str
    # The key's environment now, which an edit may have changed since the calls.
    environment: str
    requests: int
    errors: int
    # `null` when no call of the key was priced.
    cost_usd: Money | None


class FaultScenarioCountOut(BaseModel):
    scenario: FaultScenario
    count: int


class GatewayTotalsOut(BaseModel):
    """The previous window's totals, for the overview's deltas; each reads as the field of the
    same name on `GatewayOverviewOut` (`cache_hit_rate` as `cache.hit_rate`)."""

    requests: int
    errors: int
    error_rate: float | None
    p95_overhead_ms: float | None
    cache_hit_rate: float | None
    fallbacks: int
    rate_limited: int


class GatewaySeriesPointOut(BaseModel):
    bucket_start: datetime
    requests: int
    errors: int


class GatewaySeriesOut(BaseModel):
    # 5-minute buckets for a window of 1 hour or less, else hours; aligned to the UTC clock
    # (whole hours, :00, :05, ...), so the first may start before `from` and the last may run
    # past `to`: both can be partial.
    bucket: Literal["5m", "hour"]
    # Every bucket of the window, in order; one without calls reads 0 requests and 0 errors.
    points: list[GatewaySeriesPointOut]


class GatewayOverviewOut(BaseModel):
    """Gateway traffic of a window of at most 7 days.

    `errors` includes calls failed on purpose by Lab faults; `faults` says how many of the
    requests carried one. `fallbacks` and `retries` are event counts, not request counts: the
    switches to another target, and the repeated tries on one target, summed over all requests.
    A latency is `null` when no request measured it.
    """

    requests: int
    errors: int
    # `errors` over `requests`; `null` when there were no requests.
    error_rate: float | None
    cache: GatewayCacheOut
    # Total switches to another target, over all requests.
    fallbacks: int
    # Total repeated tries on the same target, over all requests.
    retries: int
    p95_overhead_ms: float | None
    p95_ttft_ms: float | None
    # p95 of a call's duration less the gateway's overhead, over calls that recorded an overhead
    # and reached a provider: the provider's time over every attempt, plus retry backoff and Lab
    # fault holds, and a stream's whole body. Cache hits and refusals are left out.
    p95_upstream_ms: float | None
    # Calls that ended rate limited, by the gateway (`rate_limit_kind` "gateway") or a provider.
    rate_limited: int
    # The same totals over the window of equal length right before this one.
    previous: GatewayTotalsOut
    series: GatewaySeriesOut
    by_target: list[GatewayTargetUsageOut]
    by_key: list[GatewayKeyUsageOut]
    faults: list[FaultScenarioCountOut]
