"""Checking a billing source's admin key before it is stored (spec §8.2).

The check is `fetchers.probe`: one bucket of yesterday's costs, 10 s at most. Its outcome is
stored as the source's `status` and `last_error`, whatever it is: a network blip at creation
must not lose the key the user just pasted. With `BILLING_SOURCES_ENABLED` off no provider is
called, and the source goes back to `never_fetched` with no `last_error`: what an earlier key's
check said must not vouch for a new key.
"""

from dataclasses import dataclass
from datetime import datetime

import httpx
import structlog

from app.config import Settings
from app.core.crypto import CryptoNotConfigured
from app.db.models.billing import BillingSource, BillingSourceKind, SourceStatus
from app.reconciliation.fetchers.probe import probe

logger = structlog.get_logger(__name__)


def require_crypto(settings: Settings) -> None:
    """Refuse an API source before anything else: CREDENTIALS_KEYS is the operator's state."""
    if not settings.is_crypto_configured:
        raise CryptoNotConfigured("CREDENTIALS_KEYS is not set")


@dataclass(frozen=True)
class KeyCheck:
    """The outcome of checking a key, as the source fields it sets."""

    status: SourceStatus
    last_error: str | None
    last_fetched_at: datetime

    def apply(self, source: BillingSource) -> None:
        source.status = self.status
        source.last_error = self.last_error
        source.last_fetched_at = self.last_fetched_at


def record_check(source: BillingSource, checked: KeyCheck | None) -> None:
    """Store a check's outcome on the source. Unchecked (provider calls off), the status goes
    back to `never_fetched`: what an earlier key's check said must not vouch for this key."""
    if checked is None:
        source.status = "never_fetched"
        source.last_error = None
    else:
        checked.apply(source)


async def check_key(
    http: httpx.AsyncClient,
    kind: BillingSourceKind,
    key: str,
    *,
    settings: Settings,
    now: datetime,
) -> KeyCheck | None:
    """Check the key with the provider; None when provider calls are off.

    `last_error` is the probe's status line (an HTTP status line or a fixed phrase), never a
    response body; the log line carries the same, never the key.
    """
    if not settings.billing_sources_enabled:
        return None
    failure = await probe(http, kind, key)
    logger.info("billing_source_key_checked", kind=kind, status_line=failure)
    return KeyCheck("failing" if failure else "ok", failure, now)
