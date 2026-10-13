"""Edges of the one model-matching rule, where the Python predicate must agree with its SQL twin.

Failure mode: Python's `$` also matches before a final newline, so `gpt-4o-latest\\n` counts as
a snapshot of `gpt-4o` in Python (pricing) while Postgres's `~` rejects it (filters, budgets).
"""

import pytest

from app.core.model_match import matches_model


def test_trailing_newline_is_not_a_snapshot_suffix() -> None:
    assert not matches_model("gpt-4o", "gpt-4o-latest\n")
    assert not matches_model("gpt-4o", "gpt-4o-20240806\n")


@pytest.mark.parametrize(
    "actual",
    ["gpt-4o", "gpt-4o-latest", "gpt-4o-20240806", "gpt-4o-2024-08-06"],
)
def test_the_name_or_a_snapshot_suffix_matches(actual: str) -> None:
    assert matches_model("gpt-4o", actual)
