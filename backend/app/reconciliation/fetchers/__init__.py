"""Provider cost-report fetchers over the vetted HTTP client (spec §10.2 steps 3-4, §12).

`fetcher_for(kind).fetch(http, api_key, day_from, day_to, provider_scope, patterns)` returns the
mapped lines of one source's window, or raises `FetchFailed` with a fixed status line; `probe`
checks a key with one bucket. Hosts are constants per source kind (`HOSTS`); nothing takes a
base URL.
"""

from collections.abc import Mapping

from app.db.models.billing import BillingSourceKind
from app.reconciliation.fetchers.anthropic import AnthropicFetcher
from app.reconciliation.fetchers.base import HOSTS, Fetcher, FetchFailed, FetchResult
from app.reconciliation.fetchers.openai import OpenAIFetcher
from app.reconciliation.fetchers.probe import probe

__all__ = [
    "FETCHERS",
    "HOSTS",
    "FetchFailed",
    "FetchResult",
    "Fetcher",
    "fetcher_for",
    "probe",
]

FETCHERS: Mapping[BillingSourceKind, Fetcher] = {
    "anthropic_admin_api": AnthropicFetcher(),
    "openai_costs_api": OpenAIFetcher(),
}


def fetcher_for(kind: BillingSourceKind) -> Fetcher:
    """The fetcher of an API source kind; `KeyError` for `csv_upload`, which never fetches."""
    return FETCHERS[kind]
