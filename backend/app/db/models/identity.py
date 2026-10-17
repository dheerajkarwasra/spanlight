"""Users, organizations, membership, sessions, invites, login throttling and the audit trail."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.core.security import UNUSABLE_PASSWORD_HASH
from app.db.models.base import Base, _pg_enum


class MembershipRole(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class AuditAction(enum.StrEnum):
    ORG_CREATE = "org.create"
    MEMBER_ADD = "member.add"
    MEMBER_ROLE_CHANGE = "member.role_change"
    MEMBER_REMOVE = "member.remove"
    INVITE_CREATE = "invite.create"
    INVITE_REVOKE = "invite.revoke"
    INVITE_ACCEPT = "invite.accept"
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    PROJECT_DELETE = "project.delete"
    KEY_CREATE = "key.create"
    KEY_REVOKE = "key.revoke"
    USER_PASSWORD_RESET = "user.password_reset"  # noqa: S105 - an action name
    USER_OAUTH_LINK = "user.oauth_link"
    USER_OAUTH_UNLINK = "user.oauth_unlink"
    USER_TOTP_ENABLE = "user.totp_enable"
    USER_TOTP_DISABLE = "user.totp_disable"
    ORG_UPDATE = "org.update"
    CREDENTIAL_CREATE = "credential.create"
    CREDENTIAL_ROTATE = "credential.rotate"
    CREDENTIAL_DELETE = "credential.delete"
    GATEWAY_ROUTE_CREATE = "gateway_route.create"
    GATEWAY_ROUTE_UPDATE = "gateway_route.update"
    GATEWAY_ROUTE_REVERT = "gateway_route.revert"
    GATEWAY_ROUTE_DELETE = "gateway_route.delete"
    GATEWAY_KEY_CREATE = "gateway_key.create"
    GATEWAY_KEY_UPDATE = "gateway_key.update"
    GATEWAY_KEY_REVOKE = "gateway_key.revoke"
    FAULT_PROFILE_CREATE = "fault_profile.create"
    FAULT_PROFILE_UPDATE = "fault_profile.update"
    FAULT_PROFILE_DELETE = "fault_profile.delete"
    PRICE_OVERRIDE_CREATE = "price_override.create"
    PRICE_OVERRIDE_DELETE = "price_override.delete"
    GATEWAY_CACHE_PURGE = "gateway_cache.purge"
    ALERT_CHANNEL_CREATE = "alert_channel.create"
    ALERT_CHANNEL_UPDATE = "alert_channel.update"
    ALERT_CHANNEL_DELETE = "alert_channel.delete"
    ALERT_CHANNEL_TEST = "alert_channel.test"
    ALERT_CHANNEL_RETRY_DELIVERY = "alert_channel.retry_delivery"
    ALERT_RULE_CREATE = "alert_rule.create"
    ALERT_RULE_UPDATE = "alert_rule.update"
    ALERT_RULE_DELETE = "alert_rule.delete"
    ALERT_RULE_MUTE = "alert_rule.mute"
    ALERT_EVENT_ACKNOWLEDGE = "alert_event.acknowledge"
    BUDGET_CREATE = "budget.create"
    BUDGET_UPDATE = "budget.update"
    BUDGET_DELETE = "budget.delete"
    INSIGHT_ACKNOWLEDGE = "insight.acknowledge"
    INSIGHT_RESOLVE = "insight.resolve"
    INSIGHT_MUTE = "insight.mute"
    INSIGHT_UNMUTE = "insight.unmute"
    INSIGHT_EXPLAIN = "insight.explain"
    PROMPT_CREATE = "prompt.create"
    PROMPT_VERSION_CREATE = "prompt.version_create"
    PROMPT_LABEL_SET = "prompt.label_set"
    PROMPT_LABEL_DELETE = "prompt.label_delete"
    PROMPT_DELETE = "prompt.delete"
    SCORE_CONFIG_CREATE = "score_config.create"
    SCORE_CONFIG_UPDATE = "score_config.update"
    SCORE_CONFIG_DELETE = "score_config.delete"
    DATASET_CREATE = "dataset.create"
    DATASET_DELETE = "dataset.delete"
    ANNOTATION_QUEUE_CREATE = "annotation_queue.create"
    ANNOTATION_QUEUE_DELETE = "annotation_queue.delete"
    EVALUATOR_CREATE = "evaluator.create"
    EVALUATOR_UPDATE = "evaluator.update"
    EVALUATOR_DELETE = "evaluator.delete"
    EXPERIMENT_CREATE = "experiment.create"
    EXPERIMENT_RUN = "experiment.run"
    BILLING_SOURCE_CREATE = "billing_source.create"
    BILLING_SOURCE_UPDATE = "billing_source.update"
    BILLING_SOURCE_ROTATE = "billing_source.rotate"
    BILLING_SOURCE_DELETE = "billing_source.delete"
    BILLING_SOURCE_REFRESH = "billing_source.refresh"
    BILLING_SOURCE_UPLOAD = "billing_source.upload"
    REVIEW_POLICY_CREATE = "review_policy.create"
    REVIEW_POLICY_UPDATE = "review_policy.update"
    REVIEW_POLICY_DELETE = "review_policy.delete"
    REVIEW_POLICY_RUN = "review_policy.run"
    REPLAY_RUN = "replay.run"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    # NULL for a user who signs in only through an OAuth provider (see `OAuthIdentity`).
    password_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # NULL until the owner of the address proves it (verification link, password reset, OAuth).
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Two-factor authentication (see `app.auth.totp_service`). The seed is sealed with
    # `app.core.crypto`; `totp_key_id` names the key it was sealed under. A secret with no
    # `totp_enabled_at` is a setup that no code has confirmed yet.
    totp_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_key_id: Mapped[str | None] = mapped_column(Text)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The 30-second step of the last code accepted; a code is only valid for a later step.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def totp_enabled(self) -> bool:
        """Whether a second factor is required to sign in (not merely being set up)."""
        return self.totp_enabled_at is not None

    @property
    def has_password(self) -> bool:
        """Whether a password can sign this user in: not an OAuth-only user, not the demo user."""
        return self.password_hash is not None and self.password_hash != UNUSABLE_PASSWORD_HASH


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # When true, members without two-factor authentication cannot use the org (see `require`).
    require_2fa: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[MembershipRole] = mapped_column(_pg_enum(MembershipRole, "membership_role"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    role: Mapped[MembershipRole] = mapped_column(_pg_enum(MembershipRole, "membership_role"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The address the invite is for, and is mailed to when email is configured; NULL for a link
    # shared by hand.
    email: Mapped[str | None] = mapped_column(CITEXT)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(CITEXT)
    ip: Mapped[str | None] = mapped_column(INET)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[str] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
