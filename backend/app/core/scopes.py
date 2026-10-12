"""What a project API key is allowed to do.

A key carries a non-empty set of scopes, chosen when it is created. The database refuses any
other value (`api_keys_scopes_check`, migration 0007), so adding a scope means a new migration
as well as a member here.
"""

import enum


class KeyScope(enum.StrEnum):
    INGEST_WRITE = "ingest:write"  # POST /v1/traces and /v1/otlp/traces
    TRACES_READ = "traces:read"  # the dashboard read routes, for the key's own project
    SCORES_WRITE = "scores:write"  # POST /v1/scores
    PROMPTS_READ = "prompts:read"  # GET /v1/prompts/{name}


# What a key gets when its creator names no scopes: the only thing keys could do before scopes.
DEFAULT_KEY_SCOPES: tuple[KeyScope, ...] = (KeyScope.INGEST_WRITE,)
