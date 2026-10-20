"""Personal access tokens: list, create (secret shown once) and revoke.

For scripts and tools that call the dashboard API as their owner. Only a signed-in session
manages tokens: a token that could mint tokens would turn one leak into a permanent foothold.
"""

import uuid

import structlog
from fastapi import APIRouter, Response, status

from app.api.deps import CurrentSession, DbSession, utcnow
from app.api.schemas import (
    PersonalAccessTokenCreatedOut,
    PersonalAccessTokenCreateIn,
    PersonalAccessTokenOut,
)
from app.auth.tokens import RevokeOutcome, issue_token, list_active_tokens, revoke_token
from app.core.errors import forbidden, not_found
from app.core.security import mark_uncacheable
from app.db.models import AuditAction
from app.services.audit import record_user_audit_in_each_org
from app.services.demo import is_demo_user

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/tokens", tags=["auth"])


@router.get("", response_model=list[PersonalAccessTokenOut])
async def list_tokens(auth: CurrentSession, db: DbSession) -> list[PersonalAccessTokenOut]:
    """The caller's tokens that can still be used. Never includes a secret."""
    tokens = await list_active_tokens(db, auth.user.id, now=utcnow())
    return [PersonalAccessTokenOut.model_validate(token) for token in tokens]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PersonalAccessTokenCreatedOut)
async def create_token(
    body: PersonalAccessTokenCreateIn, response: Response, auth: CurrentSession, db: DbSession
) -> PersonalAccessTokenCreatedOut:
    """Create a token. The 201 is the only time its secret is shown."""
    if is_demo_user(auth.user):
        # Every visitor is this one user, so a token would outlive the visit and be usable by
        # whoever saw it.
        raise forbidden("The demo account cannot create access tokens.")

    issued = await issue_token(
        db, auth.user.id, name=body.name, scope=body.scope, expires_at=body.expires_at
    )
    await record_user_audit_in_each_org(
        db,
        user_id=auth.user.id,
        action=AuditAction.TOKEN_CREATE,
        target_type="token",
        target_id=issued.token.id,
        metadata={"name": body.name, "scope": body.scope.value},
    )
    await db.commit()
    await db.refresh(issued.token)
    # Ids and settings only: never the token, not even its prefix.
    logger.info(
        "pat_created",
        user_id=str(auth.user.id),
        token_id=str(issued.token.id),
        scope=issued.token.scope.value,
        expires_at=issued.token.expires_at.isoformat() if issued.token.expires_at else None,
    )
    mark_uncacheable(response)
    return PersonalAccessTokenCreatedOut(
        **PersonalAccessTokenOut.model_validate(issued.token).model_dump(),
        token=issued.plaintext,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(token_id: uuid.UUID, auth: CurrentSession, db: DbSession) -> None:
    """Revoke a token. Repeating it is fine; a token that is not the caller's is a 404."""
    outcome = await revoke_token(db, auth.user.id, token_id, now=utcnow())
    if outcome is RevokeOutcome.NOT_FOUND:
        raise not_found()
    if outcome is RevokeOutcome.REVOKED:
        await record_user_audit_in_each_org(
            db,
            user_id=auth.user.id,
            action=AuditAction.TOKEN_REVOKE,
            target_type="token",
            target_id=token_id,
        )
    await db.commit()
    if outcome is RevokeOutcome.REVOKED:
        logger.info("pat_revoked", user_id=str(auth.user.id), token_id=str(token_id))
