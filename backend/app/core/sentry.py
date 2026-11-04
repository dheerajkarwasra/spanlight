"""Error reporting: Sentry, started by the api and the worker when SENTRY_DSN is set."""

from urllib.parse import urlsplit

import sentry_sdk
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.types import Event, Hint

from app.config import Settings

# Invite links mailed before the token moved to the fragment open /invite/<token>, so a browser can
# still send a token in the Referer of the API calls that page makes. The SDK's own filter
# removes `Authorization` and `Cookie` only; the CSRF token (session-bound, but still a credential
# for that session) and the client's Idempotency-Key are not needed to debug an error either.
_DROPPED_HEADERS = frozenset({"referer", "x-csrf-token", "idempotency-key"})


def before_send(event: Event, hint: Hint) -> Event:
    """Remove the parts of a failed request's URL that can carry a bearer secret.

    A query string can carry a bearer secret (the invite preview took its token there until it
    became a POST), and the SDK sends it as `request.query_string`. So the query is dropped for
    every request instead of for a list of known names. The path stays: it names the route.
    """
    request = event.get("request")
    if not request:
        return event
    cleaned = {key: value for key, value in request.items() if key != "query_string"}
    url = cleaned.get("url")
    if isinstance(url, str):
        try:
            cleaned["url"] = urlsplit(url)._replace(query="", fragment="").geturl()
        except ValueError:
            # A URL built from a crafted `Host` header (`[bad`) does not parse. Raising here would
            # make the SDK drop the whole event, hiding the error being reported, so only the URL
            # goes: it cannot be cleaned, and the path it was meant to keep is not worth the risk.
            del cleaned["url"]
    headers = cleaned.get("headers")
    if isinstance(headers, dict):
        cleaned["headers"] = {
            name: value
            for name, value in headers.items()
            if str(name).lower() not in _DROPPED_HEADERS
        }
    return {**event, "request": cleaned}


def init_sentry(settings: Settings) -> bool:
    """Start Sentry if a DSN is configured. Returns whether it was started."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        send_default_pii=False,
        # send_default_pii=False still lets the SDK attach request bodies, and the stack frames'
        # local variables (FastAPI keeps the parsed body in its own frame), to the event of a
        # failed request. Its scrubber only knows a few top-level key names, so a 5xx while
        # ingesting traces would send customers' prompts and completions to Sentry, and one
        # during signup or login their email addresses. Neither is worth that risk.
        max_request_body_size="never",
        include_local_variables=False,
        before_send=before_send,
        # Its breadcrumbs keep the full URL of every outgoing call (an alert channel's Slack
        # webhook URL is a secret) and it adds `sentry-trace` and `baggage` headers to requests
        # that go to customers' receivers and to LLM providers.
        disabled_integrations=[HttpxIntegration()],
    )
    return True
