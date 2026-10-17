"""The `scope_id` of a `metadata` or `trace_name` budget, parsed. Pure: no database, no I/O.

A `metadata` scope is `<key>=<value>`, split at the first `=`: the key follows the trace
metadata key pattern (which has no `=`, so the split is unambiguous) and the value is 1 to 256
characters, kept as sent, since a value with a leading space is a different value. A
`trace_name` scope is the name with surrounding whitespace removed, as the gateway trims its
`x-spanlight-trace-name` header, so the two agree on what a name is.
"""

from app.ingest.metadata import MAX_METADATA_VALUE, METADATA_KEY_PATTERN

MAX_TRACE_NAME = 256  # spec §8.1, the same bound as the `x-spanlight-trace-name` header


def parse_metadata_scope(scope_id: str) -> tuple[str, str]:
    """The `(key, value)` of a metadata scope; `ValueError` when either part is invalid."""
    key, separator, value = scope_id.partition("=")
    if not separator or METADATA_KEY_PATTERN.fullmatch(key) is None:
        raise ValueError("a metadata scope is key=value with a valid key")
    if not 1 <= len(value) <= MAX_METADATA_VALUE:
        raise ValueError(f"a metadata scope's value is 1 to {MAX_METADATA_VALUE} characters")
    return key, value


def parse_trace_name_scope(scope_id: str) -> str:
    """The trimmed trace name of a `trace_name` scope; `ValueError` when it is blank or longer
    than 256 characters once trimmed."""
    name = scope_id.strip()
    if not 1 <= len(name) <= MAX_TRACE_NAME:
        raise ValueError(f"a trace name scope is 1 to {MAX_TRACE_NAME} characters")
    return name
