"""The rate-limit response headers a span may keep, and the snapshot filter. Pure.

`provider.ratelimit` holds at most these 29 headers: OpenAI's documented rate-limit
headers, the `retry-after-ms` the OpenAI SDKs honour, and Anthropic's documented response
headers. Every other header is dropped, so credentials (`authorization`, `x-api-key`, `cookie`,
`set-cookie`) and account identifiers never reach stored data. The gateway snapshots provider
responses with this filter, ingest applies it again to whatever a client sent, and the SDK keeps
a verbatim copy in `sdks/python/src/spanlight/_ratelimit_headers.py`.

Values are informational text: printable ASCII, at most 64 characters, never acted on beyond the
rate-limit classifier's last-resort rules.
"""

from collections.abc import Mapping

RATELIMIT_HEADERS: frozenset[str] = frozenset(
    {
        # Both providers.
        "retry-after",
        # OpenAI SDKs (observed on the wire).
        "retry-after-ms",
        # OpenAI, "Rate limits in headers".
        "x-ratelimit-limit-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
        "x-ratelimit-limit-project-tokens",
        "x-ratelimit-remaining-project-tokens",
        "x-ratelimit-reset-project-tokens",
        # Anthropic, "Rate limits > Response headers".
        "anthropic-ratelimit-requests-limit",
        "anthropic-ratelimit-requests-remaining",
        "anthropic-ratelimit-requests-reset",
        "anthropic-ratelimit-tokens-limit",
        "anthropic-ratelimit-tokens-remaining",
        "anthropic-ratelimit-tokens-reset",
        "anthropic-ratelimit-input-tokens-limit",
        "anthropic-ratelimit-input-tokens-remaining",
        "anthropic-ratelimit-input-tokens-reset",
        "anthropic-ratelimit-output-tokens-limit",
        "anthropic-ratelimit-output-tokens-remaining",
        "anthropic-ratelimit-output-tokens-reset",
        "anthropic-priority-input-tokens-limit",
        "anthropic-priority-input-tokens-remaining",
        "anthropic-priority-input-tokens-reset",
        "anthropic-priority-output-tokens-limit",
        "anthropic-priority-output-tokens-remaining",
        "anthropic-priority-output-tokens-reset",
    }
)

MAX_HEADER_VALUE_CHARS = 64

# A successful call keeps only the headroom headers; reset times and `retry-after` matter only
# when a call was refused, so they are kept on failed spans alone. Anthropic puts the kind last
# (`anthropic-ratelimit-tokens-limit`), OpenAI puts it before the resource
# (`x-ratelimit-limit-tokens`), so both forms count.
SUCCESS_HEADER_SUFFIXES = ("-limit", "-remaining")
SUCCESS_HEADER_PREFIXES = ("x-ratelimit-limit-", "x-ratelimit-remaining-")


def snapshot_headers(headers: Mapping[str, str], *, failed: bool) -> dict[str, str]:
    """The allowlisted rate-limit headers of a response, with lowercase names.

    A value that is not a string of printable ASCII of at most 64 characters is dropped. On a
    successful call (`failed` false) only the limit and remaining headers are kept.
    """
    snapshot: dict[str, str] = {}
    for raw_name, value in headers.items():
        # A non-ASCII name could lowercase into an allowlisted one (U+212A KELVIN SIGN -> "k").
        if not raw_name.isascii():
            continue
        name = raw_name.lower()
        if name not in RATELIMIT_HEADERS or not _valid_value(value):
            continue
        if not failed and not _headroom(name):
            continue
        snapshot[name] = value
    return snapshot


def _valid_value(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_HEADER_VALUE_CHARS
        and all(" " <= char <= "~" for char in value)
    )


def _headroom(name: str) -> bool:
    return name.endswith(SUCCESS_HEADER_SUFFIXES) or name.startswith(SUCCESS_HEADER_PREFIXES)
