"""Checking an admin key: fetch one bucket of yesterday's costs with it (spec §8.2).

The request takes the path a fetch takes (the same host, headers, limits and egress checks);
the page deadline is `PROBE_TIMEOUT_S` for the send and the read. The outcome is `None` or a
status line from the fixed vocabulary, never a response body.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta

import httpx

from app.db.models.billing import BillingSourceKind
from app.reconciliation.fetchers import anthropic, openai
from app.reconciliation.fetchers.base import FetchFailed, PageRequest
from app.reconciliation.fetchers.pages import PageReader

PROBE_TIMEOUT_S = 10.0

_PROBES: Mapping[BillingSourceKind, Callable[[str, date], PageRequest]] = {
    "anthropic_admin_api": anthropic.probe_request,
    "openai_costs_api": openai.probe_request,
}


async def probe(
    http: httpx.AsyncClient,
    kind: BillingSourceKind,
    api_key: str,
    *,
    today: date | None = None,
) -> str | None:
    """None when the provider answers the costs of yesterday (UTC) for this key; else why not.

    `today` replaces the clock in tests. Raises `KeyError` for a kind that has no API.
    """
    day = (today or datetime.now(UTC).date()) - timedelta(days=1)
    request = _PROBES[kind](api_key, day)
    reader = PageReader(http, kind, request.headers, timeout_s=PROBE_TIMEOUT_S)
    try:
        # The reader's own deadline bounds the send and the read, and logs a timeout.
        await reader.page(request.path, request.query)
    except FetchFailed as failure:
        return failure.status_line
    return None
