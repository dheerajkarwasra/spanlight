"""Provider credentials: add, rotate, delete and check them. Callers commit.

Resealing them under a new key is `app.gateway.reseal`. Every write takes the organization's
key-share lock first (`lock_org_for_write`), then the credential row, then writes the audit
event: the lock order of `app.services.deletion`, so a concurrent organization deletion waits
instead of deadlocking.

The clear API key lives only in the request body and in the local variables of these functions.
It is never stored, logged, audited or put in an exception: the audit metadata names the
credential, and log lines carry its id.
"""

import uuid
from datetime import datetime

import httpx
import structlog
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.crypto import CryptoNotConfigured, DecryptionFailed, UnknownKeyId
from app.core.egress import Resolver
from app.db.errors import violated_constraint
from app.db.models import AuditAction, ProviderCredential
from app.gateway import queries
from app.gateway.credential_check import probe_models
from app.gateway.credentials import (
    base_url_for,
    check_base_url_host,
    decrypt_api_key,
    seal_api_key,
    validate_base_url,
)
from app.gateway.schemas import CredentialCheck, CredentialCreate
from app.services.audit import record_audit
from app.services.deletion import lock_org_for_write

logger = structlog.get_logger(__name__)

NAME_CONSTRAINT = "provider_credentials_org_name_key"
# The `last_error` column holds at most this many characters (a CHECK in migration 0200).
MAX_ERROR_LENGTH = 200


class CredentialNotFoundError(Exception):
    """No credential with this id in the organization (or the organization is gone)."""


class CredentialNameTakenError(Exception):
    """Another credential in the organization already has this name."""


class CredentialInUseError(Exception):
    """A gateway route sends calls through the credential, so it cannot be deleted."""

    def __init__(self, credential_name: str, route_name: str) -> None:
        super().__init__(f"{credential_name} is used by route {route_name}")
        self.credential_name = credential_name
        self.route_name = route_name


def _require_crypto(settings: Settings) -> None:
    # Before anything else: it is the operator's state, not something about the request.
    if not settings.is_crypto_configured:
        raise CryptoNotConfigured("CREDENTIALS_KEYS is not set")


async def org_credential(
    db: AsyncSession, org_id: uuid.UUID, credential_id: uuid.UUID
) -> ProviderCredential | None:
    """The organization's credential with this id, for callers outside the gateway (judges, the
    playground, experiments); None when the organization has none of that id."""
    return await queries.get_credential(db, org_id, credential_id)


async def create_credential(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    data: CredentialCreate,
    *,
    settings: Settings,
    ip: str | None = None,
    resolver: Resolver | None = None,
) -> ProviderCredential:
    """Seal the key and store the credential.

    Raises `CryptoNotConfigured`, `InvalidBaseUrl`, `EgressError` (the base URL's host is
    private or does not resolve), `CredentialNotFoundError` (the organization is gone) and
    `CredentialNameTakenError`.
    """
    _require_crypto(settings)
    allow_insecure = settings.gateway_allow_insecure_base_urls
    base_url = base_url_for(data.provider, data.base_url, allow_insecure=allow_insecure)
    if base_url is not None:
        await check_base_url_host(base_url, allow_insecure=allow_insecure, resolver=resolver)
    if not await lock_org_for_write(db, org_id):
        raise CredentialNotFoundError

    sealed = seal_api_key(data.api_key.get_secret_value(), settings=settings)
    credential = ProviderCredential(
        org_id=org_id,
        provider=data.provider,
        name=data.name,
        base_url=base_url,
        ciphertext=sealed.ciphertext,
        key_id=sealed.key_id,
        created_by=actor_id,
    )
    try:
        async with db.begin_nested():
            db.add(credential)
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise CredentialNameTakenError from None
        raise
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.CREDENTIAL_CREATE,
        target_type="provider_credential",
        target_id=credential.id,
        ip=ip,
        metadata={
            "name": credential.name,
            "provider": credential.provider.value,
            "base_url": base_url,
        },
    )
    return credential


async def rotate_credential(
    db: AsyncSession,
    org_id: uuid.UUID,
    credential_id: uuid.UUID,
    actor_id: uuid.UUID,
    api_key: str,
    *,
    settings: Settings,
    now: datetime,
    ip: str | None = None,
    resolver: Resolver | None = None,
) -> ProviderCredential:
    """Replace the credential's key with a new one, sealed under the active key.

    The base URL is checked again: the policy may have tightened since the credential was added.
    The last check's result belonged to the old key, so it is cleared. Raises
    `CryptoNotConfigured`, `CredentialNotFoundError`, `InvalidBaseUrl` and `EgressError`.
    """
    _require_crypto(settings)
    found = await queries.get_credential(db, org_id, credential_id)
    if found is None:
        raise CredentialNotFoundError
    base_url = found.base_url
    if base_url is not None:
        # Outside the row lock: resolving a host can take seconds.
        allow_insecure = settings.gateway_allow_insecure_base_urls
        validate_base_url(base_url, allow_insecure=allow_insecure)
        await check_base_url_host(base_url, allow_insecure=allow_insecure, resolver=resolver)

    if not await lock_org_for_write(db, org_id):
        raise CredentialNotFoundError
    credential = await queries.lock_credential(db, org_id, credential_id)
    if credential is None:
        raise CredentialNotFoundError
    sealed = seal_api_key(api_key, settings=settings)
    credential.ciphertext = sealed.ciphertext
    credential.key_id = sealed.key_id
    credential.rotated_at = now
    credential.last_checked_at = None
    credential.last_error = None
    await db.flush()
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.CREDENTIAL_ROTATE,
        target_type="provider_credential",
        target_id=credential.id,
        ip=ip,
        metadata={"name": credential.name, "provider": credential.provider.value},
    )
    return credential


async def delete_credential(
    db: AsyncSession,
    org_id: uuid.UUID,
    credential_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ip: str | None = None,
) -> None:
    """Delete the credential. Raises `CredentialNotFoundError` and `CredentialInUseError`."""
    if not await lock_org_for_write(db, org_id):
        raise CredentialNotFoundError
    credential = await queries.lock_credential(db, org_id, credential_id)
    if credential is None:
        raise CredentialNotFoundError
    route_name = await queries.credential_in_use(db, credential.id)
    if route_name is not None:
        raise CredentialInUseError(credential.name, route_name)
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.CREDENTIAL_DELETE,
        target_type="provider_credential",
        target_id=credential.id,
        ip=ip,
        metadata={"name": credential.name, "provider": credential.provider.value},
    )
    await db.delete(credential)
    await db.flush()


async def check_credential(
    db: AsyncSession,
    http: httpx.AsyncClient,
    org_id: uuid.UUID,
    credential_id: uuid.UUID,
    *,
    settings: Settings,
    now: datetime,
) -> CredentialCheck:
    """List the provider's models with the credential and record the outcome on the row.

    A key that cannot be opened (its key id left the keyring, the bytes were altered) is a failed
    check, not a server error: the credential cannot work until it is rotated. Raises
    `CryptoNotConfigured` and `CredentialNotFoundError`.
    """
    _require_crypto(settings)
    credential = await queries.get_credential(db, org_id, credential_id)
    if credential is None:
        raise CredentialNotFoundError
    # Which key this check is about; the result is only stored if it is still the current one.
    checked_rotated_at = credential.rotated_at
    error: str | None
    try:
        api_key = decrypt_api_key(credential, settings=settings)
    except (UnknownKeyId, DecryptionFailed) as failure:
        logger.warning(
            "provider_credential_unreadable",
            credential_id=str(credential.id),
            error_type=type(failure).__name__,
        )
        error = "key cannot be decrypted"
    else:
        error = await probe_models(
            http,
            credential.provider,
            credential.base_url,
            api_key,
            allow_insecure=settings.gateway_allow_insecure_base_urls,
        )
    if error is not None:
        error = error[:MAX_ERROR_LENGTH]
    # An UPDATE by id, not an attribute change: a credential deleted during the check is then
    # simply not updated, instead of failing the flush. It is also conditional on `rotated_at`
    # being what was read: a rotation that committed while the provider was answering cleared the
    # last check for the new key, and this result, which is about the old key, must not refill it.
    # A reseal keeps the key (and `rotated_at`), so its result still applies. The organization is
    # locked only now, after the provider answered, so a deletion never waits on a slow probe.
    if not await lock_org_for_write(db, org_id):
        raise CredentialNotFoundError
    await db.execute(
        update(ProviderCredential)
        .where(
            ProviderCredential.org_id == org_id,
            ProviderCredential.id == credential.id,
            ProviderCredential.rotated_at.is_not_distinct_from(checked_rotated_at),
        )
        .values(last_checked_at=now, last_error=error)
        .execution_options(synchronize_session=False)
    )
    return CredentialCheck(status="ok" if error is None else "error", checked_at=now, error=error)
