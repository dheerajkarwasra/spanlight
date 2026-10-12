"""Building the ``POST /v1/scores`` payload for one score.

Scores are recorded with :func:`spanlight.score` and sent by the background exporter. Each
score gets a UUIDv7 ``id`` when it is recorded, so a retried request can never store it twice.
"""

from __future__ import annotations

import logging
import math

from ._ids import new_uuid7
from ._serialize import JsonValue

logger = logging.getLogger("spanlight")

ScoreValue = float | int | bool | str


def build_score(
    trace_id: str,
    name: str,
    value: ScoreValue,
    *,
    span_id: str | None = None,
    comment: str | None = None,
) -> dict[str, JsonValue] | None:
    """Return the wire form of one score, or ``None`` (with a warning) when it cannot be sent.

    A ``bool`` stays a JSON boolean, which makes the server infer a boolean score stored as
    1 or 0. A ``str`` becomes ``string_value`` (a categorical score). Numbers must be finite.
    """
    if not isinstance(trace_id, str) or not isinstance(name, str):
        logger.warning("Spanlight: score() needs a string trace_id and name; score dropped")
        return None
    payload: dict[str, JsonValue] = {"id": new_uuid7(), "trace_id": trace_id, "name": name}
    if isinstance(value, bool):
        payload["value"] = value
    elif isinstance(value, int | float):
        if not math.isfinite(value):
            logger.warning("Spanlight: score %r has a non-finite value; score dropped", name)
            return None
        payload["value"] = value
    elif isinstance(value, str):
        payload["string_value"] = value
    else:
        logger.warning(
            "Spanlight: score %r has an unsupported value type %s; score dropped",
            name,
            type(value).__name__,
        )
        return None
    if span_id is not None:
        payload["span_id"] = span_id
    if comment is not None:
        payload["comment"] = comment
    return payload
