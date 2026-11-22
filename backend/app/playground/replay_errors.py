"""Why a replay is refused. The API maps the code to a `422` problem (spec §9)."""

REFUSALS: dict[str, str] = {
    "REPLAY_NOT_LLM": "Only LLM spans can be replayed.",
    "REPLAY_NO_PAYLOAD": (
        "This span has no stored request: the project did not capture payloads, or "
        "retention removed them."
    ),
    "REPLAY_PAYLOAD_TRUNCATED": (
        "The stored request was truncated at ingest, so it cannot be sent as it was."
    ),
    "REPLAY_PAYLOAD_REDACTED": (
        "The request holds redacted content ([REDACTED] or [MAX_DEPTH]). Edit it out before "
        "replaying."
    ),
    "REPLAY_UNSUPPORTED_INPUT": (
        "The stored input does not contain a usable request body for its recorded provider surface."
    ),
    "REPLAY_PROVIDER_MISMATCH": ("Pick a credential that supports the stored {provider} request."),
    "REPLAY_MODEL_UNPRICED": (
        "The replay budget needs a verified cost bound for this provider and model."
    ),
    "REPLAY_BODY_TOO_LARGE": "The edited request is larger than 256 KB.",
    "REPLAY_UNPRICED_OPTION": ("The replay budget cannot bound the cost of this option: {option}."),
}
"""The nine refusal codes and their detail copy; `REPLAY_PROVIDER_MISMATCH` is formatted with
the surface the span needs, `REPLAY_UNPRICED_OPTION` with the option."""


class ReplayRefusedError(Exception):
    """The replay cannot be built or sent; `code` is one of `REFUSALS`."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
