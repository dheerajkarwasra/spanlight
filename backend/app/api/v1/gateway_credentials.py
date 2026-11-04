"""Provider credentials: the organization's API keys for OpenAI, Anthropic and compatible servers.

Members read the list; only owners add, rotate, check and delete (`credentials:manage`). The
API key is accepted in a request body and never returned: no response, audit event or log line
carries it. Every response that describes a credential is marked uncacheable all the same.

Credential routes take no `Idempotency-Key`: a kept request would have to be compared with the
body that carried the key, and nothing about a credential may be kept outside its sealed column.
"""

import uuid
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import Access, ClockDep, DbSession, SettingsDep, client_ip, require
from app.api.schemas import (
    CredentialCheckOut,
    CredentialCreate,
    CredentialOut,
    CredentialRotateIn,
    UserOut,
)
from app.core.crypto import CryptoNotConfigured
from app.core.egress import BlockedAddress, EgressError
from app.core.errors import FieldError, ProblemError, conflict, not_configured, not_found
from app.core.permissions import Permission
from app.core.security import mark_uncacheable
from app.db.models import ProviderCredential, User
from app.gateway import queries, service
from app.gateway.credentials import InvalidBaseUrl

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs/{org_id}/credentials", tags=["gateway"])

CredentialReader = Annotated[Access, Depends(require(Permission.ORG_READ))]
CredentialManager = Annotated[Access, Depends(require(Permission.CREDENTIALS_MANAGE))]

NEEDS_KEYS = (
    "Provider credentials are encrypted with a server key that isn't set. "
    "Ask your administrator to set CREDENTIALS_KEYS."
)
BLOCKED_BASE_URL = "Private and local addresses are blocked unless your administrator allows them."
UNRESOLVABLE_BASE_URL = "The host could not be resolved."


def provider_http(request: Request) -> httpx.AsyncClient:
    """The app's upstream client (`app.gateway.http`), shared with gateway traffic.

    It is built when the app starts (its lifespan); an app that was never started has none.
    """
    client: httpx.AsyncClient | None = getattr(request.app.state, "gateway_http", None)
    if client is None:
        raise RuntimeError("the upstream client is created at startup; run the app's lifespan")
    return client


ProviderHttp = Annotated[httpx.AsyncClient, Depends(provider_http)]


def _credential_out(credential: ProviderCredential, creator: User | None) -> CredentialOut:
    return CredentialOut(
        id=credential.id,
        name=credential.name,
        provider=credential.provider,
        base_url=credential.base_url,
        created_by=UserOut.model_validate(creator) if creator else None,
        created_at=credential.created_at,
        rotated_at=credential.rotated_at,
        last_used_at=credential.last_used_at,
        last_checked_at=credential.last_checked_at,
        last_error=credential.last_error,
    )


def _base_url_problem(message: str) -> ProblemError:
    return ProblemError(
        422,
        "VALIDATION_ERROR",
        "The request is invalid.",
        errors=[FieldError(field="base_url", message=message)],
    )


def _credential_problem(error: Exception) -> ProblemError:
    """The problem response for an error the credential service raises."""
    if isinstance(error, CryptoNotConfigured):
        return not_configured(NEEDS_KEYS)
    if isinstance(error, service.CredentialNotFoundError):
        return not_found()
    if isinstance(error, service.CredentialNameTakenError):
        return conflict(
            "CREDENTIAL_NAME_TAKEN",
            "A credential with this name already exists in this organization.",
        )
    if isinstance(error, service.CredentialInUseError):
        return conflict(
            "CREDENTIAL_IN_USE",
            f"{error.credential_name} is used by route {error.route_name}. "
            "Remove it from the route first.",
        )
    if isinstance(error, InvalidBaseUrl):
        return _base_url_problem(str(error))
    if isinstance(error, BlockedAddress):
        return _base_url_problem(BLOCKED_BASE_URL)
    if isinstance(error, EgressError):
        return _base_url_problem(UNRESOLVABLE_BASE_URL)
    raise error


CREDENTIAL_ERRORS = (
    CryptoNotConfigured,
    service.CredentialNotFoundError,
    service.CredentialNameTakenError,
    service.CredentialInUseError,
    InvalidBaseUrl,
    EgressError,
)


@router.get("", response_model=list[CredentialOut], summary="List provider credentials")
async def list_credentials(
    org_id: uuid.UUID, access: CredentialReader, db: DbSession, response: Response
) -> list[CredentialOut]:
    """Newest first. Never includes a key."""
    mark_uncacheable(response)
    rows = await queries.list_credentials(db, access.org.id)
    return [_credential_out(credential, creator) for credential, creator in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CredentialOut,
    summary="Add a provider credential",
)
async def create_credential(
    org_id: uuid.UUID,
    body: CredentialCreate,
    request: Request,
    response: Response,
    access: CredentialManager,
    db: DbSession,
    settings: SettingsDep,
) -> CredentialOut:
    """Seal and store the key. `base_url` is required for `openai_compatible`, refused otherwise.

    `409 NOT_CONFIGURED` without CREDENTIALS_KEYS; `422` on `base_url` for a URL that is not
    `https://`, has userinfo, a query or a fragment, or resolves to a private address.
    """
    try:
        credential = await service.create_credential(
            db, access.org.id, access.user_id, body, settings=settings, ip=client_ip(request)
        )
    except CREDENTIAL_ERRORS as error:
        raise _credential_problem(error) from None
    await db.commit()
    await db.refresh(credential)
    logger.info(
        "provider_credential_created",
        org_id=str(access.org.id),
        credential_id=str(credential.id),
        provider=credential.provider.value,
    )
    mark_uncacheable(response)
    return _credential_out(credential, access.auth.user)


@router.post(
    "/{credential_id}/rotate", response_model=CredentialOut, summary="Replace a credential's key"
)
async def rotate_credential(
    org_id: uuid.UUID,
    credential_id: uuid.UUID,
    body: CredentialRotateIn,
    request: Request,
    response: Response,
    access: CredentialManager,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> CredentialOut:
    """Seal a new key in place of the old one. Clears the last check, which was of the old key."""
    try:
        await service.rotate_credential(
            db,
            access.org.id,
            credential_id,
            access.user_id,
            body.api_key.get_secret_value(),
            settings=settings,
            now=clock(),
            ip=client_ip(request),
        )
    except CREDENTIAL_ERRORS as error:
        raise _credential_problem(error) from None
    await db.commit()
    # This process's gateway calls read the change from the next call, not after the key cache TTL.
    request.app.state.gateway_key_cache.clear()
    logger.info(
        "provider_credential_rotated", org_id=str(access.org.id), credential_id=str(credential_id)
    )
    found = await queries.get_credential_with_creator(db, access.org.id, credential_id)
    if found is None:
        raise not_found()
    mark_uncacheable(response)
    return _credential_out(*found)


@router.post(
    "/{credential_id}/check", response_model=CredentialCheckOut, summary="Check a credential"
)
async def check_credential(
    org_id: uuid.UUID,
    credential_id: uuid.UUID,
    response: Response,
    access: CredentialManager,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
    http: ProviderHttp,
) -> CredentialCheckOut:
    """List the provider's models with the key and record the outcome as the last check.

    A failing key is a `200` with `status: "error"` and a short reason such as `401 Unauthorized`.
    """
    try:
        result = await service.check_credential(
            db, http, access.org.id, credential_id, settings=settings, now=clock()
        )
    except CREDENTIAL_ERRORS as error:
        raise _credential_problem(error) from None
    await db.commit()
    logger.info(
        "provider_credential_checked",
        org_id=str(access.org.id),
        credential_id=str(credential_id),
        status=result.status,
    )
    mark_uncacheable(response)
    return CredentialCheckOut.model_validate(result)


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a provider credential",
)
async def delete_credential(
    org_id: uuid.UUID,
    credential_id: uuid.UUID,
    request: Request,
    access: CredentialManager,
    db: DbSession,
) -> None:
    """`409 CREDENTIAL_IN_USE` while a gateway route sends calls through it."""
    try:
        await service.delete_credential(
            db, access.org.id, credential_id, access.user_id, ip=client_ip(request)
        )
    except CREDENTIAL_ERRORS as error:
        raise _credential_problem(error) from None
    await db.commit()
    request.app.state.gateway_key_cache.clear()
    logger.info(
        "provider_credential_deleted", org_id=str(access.org.id), credential_id=str(credential_id)
    )
