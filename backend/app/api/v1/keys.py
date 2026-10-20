"""Project API keys: list, create (secret shown once) and revoke."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select

from app.api.deps import Access, DbSession, client_ip, require, utcnow
from app.api.schemas import ApiKeyCreatedOut, ApiKeyCreateIn, ApiKeyOut, UserOut
from app.core.errors import forbidden, not_found
from app.core.permissions import Permission
from app.core.scopes import KeyScope
from app.core.security import API_KEY_PREFIX, generate_key, mark_uncacheable
from app.db.models import ApiKey, AuditAction, User
from app.services.audit import record_audit
from app.services.deletion import lock_project_for_write

router = APIRouter(tags=["keys"])

KeyReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
KeyCreator = Annotated[Access, Depends(require(Permission.KEY_CREATE))]
KeyRevoker = Annotated[Access, Depends(require(Permission.KEY_REVOKE_OWN))]


def _key_out(key: ApiKey, creator: User | None) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        scopes=[KeyScope(scope) for scope in key.scopes],
        created_by=UserOut.model_validate(creator) if creator else None,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        revoked_at=key.revoked_at,
    )


@router.get("/projects/{project_id}/keys", response_model=list[ApiKeyOut])
async def list_keys(project_id: uuid.UUID, access: KeyReader, db: DbSession) -> list[ApiKeyOut]:
    project = access.require_project()
    rows = (
        await db.execute(
            select(ApiKey, User)
            .outerjoin(User, User.id == ApiKey.created_by)
            .where(ApiKey.project_id == project.id)
            .order_by(ApiKey.created_at.desc())
        )
    ).all()
    return [_key_out(key, creator) for key, creator in rows]


@router.post(
    "/projects/{project_id}/keys",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiKeyCreatedOut,
)
async def create_key(
    project_id: uuid.UUID,
    body: ApiKeyCreateIn,
    request: Request,
    response: Response,
    access: KeyCreator,
    db: DbSession,
) -> ApiKeyCreatedOut:
    project = access.require_project()
    # The organization is locked before the project row is referenced, the order deletions use.
    if not await lock_project_for_write(db, access.org.id, project.id):
        raise not_found()
    generated = generate_key(API_KEY_PREFIX)
    key = ApiKey(
        project_id=project.id,
        name=body.name,
        prefix=generated.prefix,
        secret_hash=generated.secret_hash,
        created_by=access.user_id,
        scopes=[scope.value for scope in body.scopes],
        expires_at=body.expires_at,
    )
    db.add(key)
    await db.flush()
    await record_audit(
        db,
        org_id=access.org.id,
        actor_user_id=access.user_id,
        action=AuditAction.KEY_CREATE,
        target_type="api_key",
        target_id=key.id,
        ip=client_ip(request),
        metadata={
            "name": key.name,
            "prefix": key.prefix,
            "project_id": str(project.id),
            "scopes": key.scopes,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        },
    )
    await db.commit()
    await db.refresh(key)
    mark_uncacheable(response)  # the secret is shown once
    return ApiKeyCreatedOut(
        **_key_out(key, access.auth.user).model_dump(), secret=generated.plaintext
    )


@router.delete("/projects/{project_id}/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    request: Request,
    access: KeyRevoker,
    db: DbSession,
) -> None:
    project = access.require_project()
    if not await lock_project_for_write(db, access.org.id, project.id):
        raise not_found()
    key = await db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project.id)
    )
    if key is None:
        raise not_found()
    if key.created_by != access.user_id and not access.can(Permission.KEY_REVOKE_ANY):
        raise forbidden("Members can only revoke keys they created.")
    if key.revoked_at is not None:
        return

    key.revoked_at = utcnow()
    await record_audit(
        db,
        org_id=access.org.id,
        actor_user_id=access.user_id,
        action=AuditAction.KEY_REVOKE,
        target_type="api_key",
        target_id=key.id,
        ip=client_ip(request),
        metadata={"name": key.name, "prefix": key.prefix, "project_id": str(project.id)},
    )
    await db.commit()
    # After the commit, so the next lookup on this process reads the revoked row. Other api
    # processes see the revocation within `API_KEY_CACHE_TTL_SECONDS`.
    request.app.state.key_cache.evict(key.prefix)
