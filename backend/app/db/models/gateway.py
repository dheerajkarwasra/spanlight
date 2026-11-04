"""The LLM gateway: provider credentials, the routes that send calls through them, and the keys
applications call it with."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum


class ProviderKind(enum.StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    # Any server that speaks the OpenAI API at a base URL of its own (vLLM, Ollama, a proxy).
    OPENAI_COMPATIBLE = "openai_compatible"


class FaultScenario(enum.StrEnum):
    """A failure the gateway can inject (the Integration Lab); each has its own params."""

    AUTH_EXPIRED = "auth_expired"
    SCOPE_DENIED = "scope_denied"
    RATE_LIMITED = "rate_limited"
    UNSUPPORTED_PARAMETER = "unsupported_parameter"
    PROVIDER_5XX = "provider_5xx"
    MALFORMED_JSON = "malformed_json"
    TRUNCATED_STREAM = "truncated_stream"
    SLOW_RESPONSE = "slow_response"
    TIMEOUT = "timeout"


class ProviderCredential(Base):
    """An organization's API key for one provider, sealed with `app.core.crypto`.

    Scoped to the organization and not under row-level security: every query filters by
    `org_id`. The migration's CHECK requires `base_url` for `openai_compatible` and forbids it
    for the two fixed providers. `ciphertext` and `key_id` together are a `Sealed` value; the
    clear key is never stored and never returned by the API.
    """

    __tablename__ = "provider_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    provider: Mapped[ProviderKind] = mapped_column(_pg_enum(ProviderKind, "provider_kind"))
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The latest check (`POST .../check`): when it ran and, if it failed, a short status line.
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        # The default repr would include the sealed bytes; ids are all a log line needs.
        return f"ProviderCredential(id={self.id!s}, org_id={self.org_id!s})"


class GatewayRoute(Base):
    """Which credentials serve a project's gateway calls, and what happens when one fails.

    Row-level security applies, as for `traces`. `config` is a `RouteConfig` as JSON
    (`app.gateway.route_config`); `version` counts saves from 1, and each save is also kept in
    `gateway_route_versions`. A partial unique index allows one `is_default` route per project.
    """

    __tablename__ = "gateway_routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GatewayRouteVersion(Base):
    """One saved config of a route. Row-level security applies, as for `traces`.

    `project_id` repeats the route's, so the policy needs no join; the composite foreign key
    keeps the two equal.
    """

    __tablename__ = "gateway_route_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["route_id", "project_id"],
            ["gateway_routes.id", "gateway_routes.project_id"],
            ondelete="CASCADE",
            name="gateway_route_versions_route_fkey",
        ),
    )

    route_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GatewayKey(Base):
    """A key an application sends to the gateway (`spl_gw_…`). Row-level security applies.

    Besides the project policy pair, one read-only policy shows the row whose `prefix` the
    gateway was presented, so the key is found before its project is known (migration 0202,
    `app.gateway.key_context`). `route_id` is NULL only once the key is revoked and its route is
    deleted. Empty `allowed_models` means any model; `None` limits mean no limit, and a `None`
    `cache_ttl_seconds` means the cache is off for the key.
    """

    __tablename__ = "gateway_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["route_id", "project_id"],
            ["gateway_routes.id", "gateway_routes.project_id"],
            ondelete="RESTRICT",
            name="gateway_keys_route_fkey",
        ),
        # The migration is the DDL source of truth: there it is `SET NULL (fault_profile_id)`, so
        # `project_id` stays; SQLAlchemy cannot compile the column list.
        ForeignKeyConstraint(
            ["fault_profile_id", "project_id"],
            ["fault_profiles.id", "fault_profiles.project_id"],
            ondelete="SET NULL",
            name="gateway_keys_fault_profile_fkey",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    route_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    name: Mapped[str] = mapped_column(Text)
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary)
    environment: Mapped[str] = mapped_column(Text)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    tpm_limit: Mapped[int | None] = mapped_column(Integer)
    allowed_models: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    default_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    cache_ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    # The Integration Lab profile this key runs, if any; never set while `environment` is
    # `production` (enforced by the key service). Deleting the profile clears it.
    fault_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        # The default repr would include the secret's hash; ids are all a log line needs.
        return f"GatewayKey(id={self.id!s}, project_id={self.project_id!s})"


class GatewayKeyMinute(Base):
    """A key's requests and tokens in one clock minute. Row-level security applies.

    `project_id` repeats the key's, so the policy needs no join; the composite foreign key keeps
    the two equal.
    """

    __tablename__ = "gateway_key_minutes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["key_id", "project_id"],
            ["gateway_keys.id", "gateway_keys.project_id"],
            ondelete="CASCADE",
            name="gateway_key_minutes_key_fkey",
        ),
    )

    key_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    minute_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    requests: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class GatewayCacheEntry(Base):
    """A stored gateway response, replayed byte for byte on an identical request.

    Row-level security applies, as for `traces`. `cache_key` is the SHA-256 of the request
    (`app.gateway.cache_key`); the project is not part of it, so the primary key is what keeps
    two projects' identical requests apart. `usage` is the original call's `Usage` as JSON. The
    migration's CHECKs hold the key to 32 bytes and `body` to 1 MB.
    """

    __tablename__ = "gateway_cache"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    cache_key: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    model: Mapped[str] = mapped_column(Text)
    body: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(Text)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # shortcut: no longer written (a hit is read-only; the hit rate comes from the spans) and
    # stays 0; drop the column in the next migration that touches this table.
    hit_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class FaultProfile(Base):
    """One failure the Integration Lab injects into calls made with the keys that run it.

    Row-level security applies, as for `traces`. `params` is the scenario's params model as JSON
    (`app.gateway.fault_params`); `probability` is the chance, from 0 to 1, that a call gets the
    fault. A disabled profile, or one past `expires_at`, is inert.
    """

    __tablename__ = "fault_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    scenario: Mapped[FaultScenario] = mapped_column(_pg_enum(FaultScenario, "fault_scenario"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    probability: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default=text("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
