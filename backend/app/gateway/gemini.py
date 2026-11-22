"""The model identifiers and request normalization shared by Gemini REST callers."""

import re
from typing import Any

from app.gateway.errors import invalid_request
from app.gateway.sse import as_int, dict_at

_MODEL_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,255}")


def valid_model_id(model: str) -> bool:
    return _MODEL_ID.fullmatch(model) is not None


def candidate_count(body: dict[str, Any]) -> int:
    return max(1, as_int(dict_at(body, "generationConfig").get("candidateCount")) or 1)


def normalize_request(body: dict[str, Any], model: str, *, stream: bool) -> None:
    if not valid_model_id(model):
        raise invalid_request("Invalid Gemini model identifier.", param="model")
    if "model" in body and body["model"] not in (model, f"models/{model}"):
        raise invalid_request("The body model must match the Gemini URL.", param="model")
    if "stream" in body and body["stream"] is not stream:
        raise invalid_request("The body stream flag must match the Gemini action.", param="stream")
    body.update(model=model, stream=stream)
