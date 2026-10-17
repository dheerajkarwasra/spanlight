"""Resealing provider credentials and billing source admin keys under the active key of
CREDENTIALS_KEYS.

A batch command (`spanlight reseal-credentials`), run after a new key is added to the keyring:
only once nothing is sealed under an older key may that key leave the keyring. It walks
`provider_credentials`, then the billing sources that hold an admin key (a CSV source holds
none), and commits per batch. A clear key exists only in local variables while a row is resealed.
"""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.crypto import (
    CryptoNotConfigured,
    DecryptionFailed,
    Sealed,
    UnknownKeyId,
    decrypt,
    encrypt,
    parse_keyring,
)
from app.db.models import BillingSource, ProviderCredential
from app.gateway import queries

logger = structlog.get_logger(__name__)

RESEAL_BATCH_SIZE = 100


@dataclass(frozen=True)
class ResealResult:
    # Provider credentials moved to the active key.
    resealed: int
    # Rows of either table that could not be opened with any key in the keyring; they keep
    # their old seal.
    failed: int
    # Rows of either table still sealed under a key other than the active one once the pass
    # ended, counted afresh: the failed ones plus any written under an older key meanwhile. Zero
    # means nothing needs the older keys any more.
    remaining: int
    # Billing sources whose admin key moved to the active key.
    billing_sources_resealed: int


@dataclass(frozen=True)
class _Table:
    model: queries.SealedTable
    # The warning logged, with the row's id under `id_field`, when no key opens a row.
    failure_event: str
    id_field: str


_CREDENTIALS = _Table(ProviderCredential, "provider_credential_reseal_failed", "credential_id")
_BILLING_SOURCES = _Table(BillingSource, "billing_source_reseal_failed", "source_id")


async def reseal_credentials(
    db: AsyncSession, *, settings: Settings, batch_size: int = RESEAL_BATCH_SIZE
) -> ResealResult:
    """Seal every provider credential and every billing source admin key under the active key
    of CREDENTIALS_KEYS, one batch at a time.

    Commits after each batch. Rows another transaction holds are waited for, never skipped. A
    row that no key in the keyring opens is counted, logged by id and left as it is. The result's
    `remaining` is a fresh count over both tables after the pass: only when it is zero may an
    older key leave the keyring. Raises `CryptoNotConfigured`.
    """
    if settings.credentials_keys is None:
        raise CryptoNotConfigured("CREDENTIALS_KEYS is not set")
    active_key_id = parse_keyring(settings.credentials_keys.get_secret_value()).active_id
    credentials, credentials_failed = await _reseal_table(
        db, _CREDENTIALS, settings=settings, active_key_id=active_key_id, batch_size=batch_size
    )
    sources, sources_failed = await _reseal_table(
        db, _BILLING_SOURCES, settings=settings, active_key_id=active_key_id, batch_size=batch_size
    )
    remaining = 0
    for table in (_CREDENTIALS, _BILLING_SOURCES):
        remaining += await queries.count_rows_not_sealed_under(db, table.model, active_key_id)
    await db.commit()
    return ResealResult(
        resealed=credentials,
        failed=credentials_failed + sources_failed,
        remaining=remaining,
        billing_sources_resealed=sources,
    )


async def _reseal_table(
    db: AsyncSession, table: _Table, *, settings: Settings, active_key_id: str, batch_size: int
) -> tuple[int, int]:
    """Reseal the rows of one table; returns how many were resealed and how many failed."""
    resealed = 0
    failed: list[uuid.UUID] = []
    while True:
        batch = await queries.rows_not_sealed_under(
            db, table.model, active_key_id, exclude=failed, limit=batch_size
        )
        if not batch:
            return resealed, len(failed)
        for row in batch:
            if row.ciphertext is None or row.key_id is None:
                # Never listed (the query reads sealed rows only); excluded so it cannot repeat.
                failed.append(row.id)
                continue
            try:
                plaintext = decrypt(
                    Sealed(ciphertext=row.ciphertext, key_id=row.key_id), settings=settings
                )
            except (UnknownKeyId, DecryptionFailed) as failure:
                logger.warning(
                    table.failure_event,
                    **{table.id_field: str(row.id)},
                    error_type=type(failure).__name__,
                )
                failed.append(row.id)
                continue
            sealed = encrypt(plaintext, settings=settings)
            row.ciphertext = sealed.ciphertext
            row.key_id = sealed.key_id
            resealed += 1
        await db.commit()
