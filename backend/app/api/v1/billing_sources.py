"""Billing sources: the provider accounts an organization reconciles its spend against (§8.2).

Members read them; admins and owners add, edit, rotate and delete them
(`billing_sources:manage`). An admin key is accepted in a request body and never returned: no
response, audit event or log line carries it. Every response that describes a source is marked
uncacheable all the same.

Adding or rotating a source that fetches from a provider checks its key with the provider, so
those checks are throttled per organization (`BILLING_KEY_CHECK_LIMITER`): a loop over them would
otherwise test admin keys against the provider from the server's address.

No route takes an `Idempotency-Key`: a kept request would have to be compared with the body that
carried the key, and nothing about a key may be kept outside its sealed column (the reasoning of
`gateway_credentials`).
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import Access, ClockDep, DbSession, SettingsDep, client_ip, require
from app.api.schemas import (
    BillingSourceCreate,
    BillingSourceOut,
    BillingSourceRotateIn,
    BillingSourceUpdate,
    SourceProjectOut,
    UserOut,
)
from app.api.v1.billing_source_problems import (
    SOURCE_ERRORS,
    source_problem,
    throttle_key_check,
)
from app.api.v1.gateway_credentials import ProviderHttp
from app.core.errors import not_found
from app.core.permissions import Permission
from app.core.security import mark_uncacheable
from app.reconciliation import queries, service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs/{org_id}/billing-sources", tags=["billing-sources"])

OrgReader = Annotated[Access, Depends(require(Permission.ORG_READ))]
SourceManager = Annotated[Access, Depends(require(Permission.BILLING_SOURCES_MANAGE))]


def _source_out(view: queries.SourceView) -> BillingSourceOut:
    source = view.source
    return BillingSourceOut(
        id=source.id,
        kind=source.kind,
        provider=source.provider,
        name=source.name,
        provider_scope=source.provider_scope,
        enabled=source.enabled,
        status=source.status,
        key_set=source.ciphertext is not None,
        key_id=source.key_id,
        last_fetched_at=source.last_fetched_at,
        last_success_at=source.last_success_at,
        last_error=source.last_error,
        projects=[
            SourceProjectOut(id=project.id, name=project.name, slug=project.slug)
            for project in view.projects
        ],
        created_by=UserOut.model_validate(view.creator) if view.creator else None,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _described(
    db: DbSession, org_id: uuid.UUID, source_id: uuid.UUID, response: Response
) -> BillingSourceOut:
    """The source as stored now (server defaults included), marked uncacheable."""
    source = await queries.source_for_org(db, org_id, source_id)
    if source is None:
        raise not_found()
    [view] = await queries.source_views(db, org_id, [source])
    mark_uncacheable(response)
    return _source_out(view)


def _logged(event: str, access: Access, source: BillingSourceOut) -> BillingSourceOut:
    logger.info(
        event,
        org_id=str(access.org.id),
        source_id=str(source.id),
        kind=source.kind,
        status=source.status,
    )
    return source


@router.get("", response_model=list[BillingSourceOut], summary="List billing sources")
async def list_sources(
    org_id: uuid.UUID, access: OrgReader, db: DbSession, response: Response
) -> list[BillingSourceOut]:
    """Lists the organization's billing sources, ordered by name.

    The list is not paginated, because an organization has only a few sources. No response
    includes an admin key; `key_set` says whether one is stored.
    """
    sources = await queries.sources_for_org(db, access.org.id)
    views = await queries.source_views(db, access.org.id, sources)
    mark_uncacheable(response)
    return [_source_out(view) for view in views]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=BillingSourceOut,
    summary="Add a billing source",
)
async def create_source(
    org_id: uuid.UUID,
    body: BillingSourceCreate,
    request: Request,
    response: Response,
    access: SourceManager,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
    http: ProviderHttp,
) -> BillingSourceOut:
    """Adds a billing source and answers `201` with it.

    For a source that fetches from a provider, the admin key is encrypted with `CREDENTIALS_KEYS`
    and checked once with the provider (one request, 10 seconds at most). The source is stored
    whatever the outcome of the check, which sets `status` and `last_error`. The key is never
    returned, logged or audited.

    Errors: `409 NOT_CONFIGURED` when the kind fetches from a provider and `CREDENTIALS_KEYS` is
    not set. `422 VALIDATION_ERROR` on `admin_key` when the key is not an Admin API key of the
    kind's provider, on `provider` when it contradicts the kind, and on `project_ids` when a
    project belongs to another organization. `409 BILLING_SOURCE_NAME_TAKEN` when another source
    of the organization has the name. `409 BILLING_SOURCE_PROJECT_TAKEN` when another source of
    the same provider already covers one of the projects. `429 RATE_LIMITED` with `Retry-After`
    past five key checks in the organization, then one a minute (creates and rotations count
    together; a request refused before the check, a CSV source among them, spends nothing).
    """
    try:
        source = await service.create_source(
            db,
            access.org.id,
            access.user_id,
            body,
            settings=settings,
            http=http,
            now=clock(),
            ip=client_ip(request),
            before_check=lambda: throttle_key_check(request, access.org.id, settings),
        )
    except SOURCE_ERRORS as error:
        raise source_problem(error) from None
    await db.commit()
    described = await _described(db, access.org.id, source.id, response)
    return _logged("billing_source_created", access, described)


@router.get("/{source_id}", response_model=BillingSourceOut, summary="Get a billing source")
async def get_source(
    org_id: uuid.UUID, source_id: uuid.UUID, access: OrgReader, db: DbSession, response: Response
) -> BillingSourceOut:
    """Returns one billing source. The admin key is never included."""
    return await _described(db, access.org.id, source_id, response)


@router.patch("/{source_id}", response_model=BillingSourceOut, summary="Edit a billing source")
async def update_source(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    body: BillingSourceUpdate,
    request: Request,
    response: Response,
    access: SourceManager,
    db: DbSession,
    clock: ClockDep,
) -> BillingSourceOut:
    """Changes the fields sent: `name`, `provider_scope`, `enabled` and `project_ids`.

    `project_ids` replaces the whole mapping, and `provider_scope: null` clears the scope. A
    source that is turned off is not fetched and cannot be refreshed, and its report stays
    readable. The name and project errors of `POST` apply here too.
    """
    try:
        await service.update_source(
            db, access.org.id, source_id, access.user_id, body, now=clock(), ip=client_ip(request)
        )
    except SOURCE_ERRORS as error:
        raise source_problem(error) from None
    await db.commit()
    described = await _described(db, access.org.id, source_id, response)
    return _logged("billing_source_updated", access, described)


@router.post(
    "/{source_id}/rotate", response_model=BillingSourceOut, summary="Replace a source's admin key"
)
async def rotate_source(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    body: BillingSourceRotateIn,
    request: Request,
    response: Response,
    access: SourceManager,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
    http: ProviderHttp,
) -> BillingSourceOut:
    """Replaces the admin key of a source that fetches from a provider.

    The new key is encrypted and checked once, as on create, and the check sets `status` and
    `last_error`. Errors: `422 VALIDATION_ERROR` on `kind` for a CSV source, which holds no key,
    and on `admin_key` when the key is not an Admin API key of the source's provider; `409
    NOT_CONFIGURED` when `CREDENTIALS_KEYS` is not set. `429 RATE_LIMITED` with `Retry-After`
    under the key-check limit of `POST`, which rotations share; a refused request spends nothing.
    """
    try:
        await service.rotate_source(
            db,
            access.org.id,
            source_id,
            access.user_id,
            body.admin_key,
            settings=settings,
            http=http,
            now=clock(),
            ip=client_ip(request),
            before_check=lambda: throttle_key_check(request, access.org.id, settings),
        )
    except SOURCE_ERRORS as error:
        raise source_problem(error) from None
    await db.commit()
    described = await _described(db, access.org.id, source_id, response)
    return _logged("billing_source_rotated", access, described)


@router.delete(
    "/{source_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a billing source"
)
async def delete_source(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    request: Request,
    access: SourceManager,
    db: DbSession,
) -> None:
    """Deletes the source with its project mappings, fetch runs and provider cost lines.

    This cannot be undone, and the source's reconciliation report stops working.
    """
    try:
        await service.delete_source(
            db, access.org.id, source_id, access.user_id, ip=client_ip(request)
        )
    except SOURCE_ERRORS as error:
        raise source_problem(error) from None
    await db.commit()
    logger.info("billing_source_deleted", org_id=str(access.org.id), source_id=str(source_id))
