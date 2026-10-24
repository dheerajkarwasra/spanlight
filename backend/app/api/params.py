"""Constraints shared by query and path parameters."""

from typing import Annotated

from fastapi import Query

NO_NUL = r"^[^\x00]*$"
"""A text parameter without NUL: Postgres text cannot hold it, so binding one would fail.

Ingestion strips NUL from stored text, so a filter that contains one could never match anyway;
it is refused with `422 VALIDATION_ERROR` before it reaches the database.
"""

Environment = Annotated[str | None, Query(max_length=64, pattern=NO_NUL)]
"""An `environment` filter, at most `TraceIn.environment`'s 64 characters; `environment=` names
the empty environment."""
