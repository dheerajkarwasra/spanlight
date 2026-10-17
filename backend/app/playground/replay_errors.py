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
        "The stored input is not a provider request body (a model with messages or input), "
        "so there is nothing to send as it was."
    ),
    "REPLAY_PROVIDER_MISMATCH": (
        "Replay sends the stored request as it is. Pick a {provider} credential, or open the "
        "messages in the playground to try another provider (not an exact replay)."
    ),
    "REPLAY_MODEL_UNPRICED": (
        "No price is known for this model, so the replay budget could not count it."
    ),
    "REPLAY_BODY_TOO_LARGE": "The edited request is larger than 256 KB.",
}
"""The eight refusal codes and their detail copy; `REPLAY_PROVIDER_MISMATCH` is formatted with
the credential family the span needs."""


class ReplayRefusedError(Exception):
    """The replay cannot be built or sent; `code` is one of `REFUSALS`."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
