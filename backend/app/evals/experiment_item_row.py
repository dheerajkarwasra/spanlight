"""The stored form of an experiment item row. Pure: no database, no I/O."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON

from app.core.text import clean_text

if TYPE_CHECKING:
    from app.db.models import DatasetItem

MAX_ERROR_CHARS = 500


def storable_row(values: dict[str, Any]) -> dict[str, Any]:
    """`values` the columns accept: errors at most 500 characters, no NUL in stored text."""
    clean = dict(values)
    for column in ("error", "evaluator_error"):
        if isinstance(clean.get(column), str):
            clean[column] = clean_text(clean[column])[:MAX_ERROR_CHARS]
    output = clean.get("output")
    if isinstance(output, dict):
        clean["output"] = {k: clean_text(v) if isinstance(v, str) else v for k, v in output.items()}
    return clean


def copy_of(item: "DatasetItem") -> dict[str, Any]:
    """The dataset item as the run read it, for its row. A JSON null input stays JSON null,
    because a NULL `input` marks a row stored before copies."""
    return {
        "input": JSON.NULL if item.input is None else item.input,
        "expected_output": item.expected_output,
        "item_metadata": item.metadata_,
    }


def json_score(value: bool | Decimal | None, string_value: str | None) -> Any:
    """A score as the item's `scores` JSON holds it: a boolean, a number or a category."""
    if isinstance(value, bool):
        return value
    if value is not None:
        return float(value)
    return string_value
