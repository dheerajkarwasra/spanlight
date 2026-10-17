"""Trace metadata rules: validation, coercion, the 32-key cap and the merge (spec §4.2, §4.4, §4.6).

The cases live in `sdks/fixtures/metadata-cases.json`, shared with the SDK's test so both sides
clean metadata the same way. The merge cases also run through the SQL twin
`merge_trace_metadata` (migration 0800), so the in-batch merge and the cross-batch upsert agree.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest.metadata import (
    MetadataDrops,
    clean_metadata,
    merge_metadata,
    metadata_from_attributes,
    record_metadata_drops,
)

FIXTURE = Path(__file__).resolve().parents[3] / "sdks" / "fixtures" / "metadata-cases.json"
CASES: dict[str, list[dict[str, Any]]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
CLEAN = CASES["clean"]
MERGE = CASES["merge"]


def _drops(counts: dict[str, int]) -> MetadataDrops:
    return MetadataDrops(**counts)


@pytest.mark.parametrize("case", CLEAN, ids=[case["name"] for case in CLEAN])
def test_clean_cases(case: dict[str, Any]) -> None:
    lenient, drops = clean_metadata(case["raw"], strict=False)
    assert lenient == case["lenient"]
    assert drops == _drops(case["drops"])

    if case["strict_error"] is None:
        strict, strict_drops = clean_metadata(case["raw"], strict=True)
        assert strict == case["lenient"]
        assert strict_drops.total == 0
    else:
        with pytest.raises(ValueError, match=r"^trace\.metadata") as raised:
            clean_metadata(case["raw"], strict=True)
        assert str(raised.value) == case["strict_error"]


def test_fixture_covers_the_review_focus_cases() -> None:
    names = {case["name"] for case in CLEAN}
    assert {"value_256_multibyte_ok", "value_257_multibyte_invalid"} <= names
    assert "key_with_dot_and_dash_ok" in names


def test_fixture_feeds_an_exponent_literal() -> None:
    # The parsed value is the float 1000.0 either way; only the file text proves the case sends
    # `1e3` (spec §12), so a re-dump of the fixture that rewrites it fails here.
    assert '"limit": 1e3' in FIXTURE.read_text(encoding="utf-8")


def test_non_finite_numbers_are_invalid_values() -> None:
    # JSON has no NaN or infinity, but Python's `json.loads` reads them.
    raw = {"ratio": float("nan"), "limit": float("inf"), "team": "ml"}

    cleaned, drops = clean_metadata(raw, strict=False)

    assert cleaned == {"team": "ml"}
    assert drops == MetadataDrops(value=2)
    with pytest.raises(
        ValueError, match=r"^trace\.metadata\.limit: value must be a finite number$"
    ):
        clean_metadata(raw, strict=True)


@pytest.mark.parametrize("case", MERGE, ids=[case["name"] for case in MERGE])
def test_merge_cases(case: dict[str, Any]) -> None:
    assert merge_metadata(case["current"], case["incoming"]) == case["expected"]


@pytest.mark.parametrize("case", MERGE, ids=[case["name"] for case in MERGE])
def test_merge_is_idempotent(case: dict[str, Any]) -> None:
    merged = merge_metadata(case["current"], case["incoming"])

    assert merge_metadata(merged, case["incoming"]) == merged


def test_merge_leaves_its_inputs_unchanged() -> None:
    current = {"a": "1"}
    incoming = {"a": "2", "b": "3"}

    merge_metadata(current, incoming)

    assert current == {"a": "1"}
    assert incoming == {"a": "2", "b": "3"}


async def test_merge_matches_sql_twin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    statement = text(
        "SELECT CAST(merge_trace_metadata(CAST(:current AS jsonb), CAST(:incoming AS jsonb)) "
        "AS text)"
    )
    async with session_factory() as db:
        for case in MERGE:
            result = await db.execute(
                statement,
                {"current": json.dumps(case["current"]), "incoming": json.dumps(case["incoming"])},
            )
            merged = json.loads(result.scalar_one())
            assert merged == case["expected"], case["name"]
            assert merged == merge_metadata(case["current"], case["incoming"]), case["name"]

            again = await db.execute(
                statement,
                {"current": json.dumps(merged), "incoming": json.dumps(case["incoming"])},
            )
            assert json.loads(again.scalar_one()) == merged, case["name"]


def test_metadata_from_attributes_lowercases_and_drops() -> None:
    prefix = "spanlight.metadata."
    attributes: dict[str, Any] = {
        "spanlight.metadata.Customer_ID": "acme",
        "spanlight.metadata.seats": 3,
        "spanlight.metadata.bad key": "x",
        "spanlight.metadata.": "no key after the prefix",
        "spanlight.metadata.plan": {"tier": "gold"},
        "spanlight.metadata.note": "",
        "spanlight.metadata.gone": None,
        "gen_ai.request.model": "gpt-5",
        "metadata.team": "not under the prefix",
    }
    drops = MetadataDrops(count=1)

    metadata = metadata_from_attributes(attributes, prefix=prefix, drops=drops)

    assert metadata == {"customer_id": "acme", "seats": "3"}
    # Counts add to what the caller's tally already held.
    assert drops == MetadataDrops(key=2, value=1, type=1, count=1)


def test_metadata_from_attributes_with_a_foreign_prefix() -> None:
    attributes = {
        "ai.telemetry.metadata.userId": "u-1",
        "ai.telemetry.functionId": "chat",
        "spanlight.metadata.team": "ml",
    }
    drops = MetadataDrops()

    metadata = metadata_from_attributes(attributes, prefix="ai.telemetry.metadata.", drops=drops)

    assert metadata == {"userid": "u-1"}
    assert drops.total == 0


def test_metadata_from_attributes_prefers_the_exact_lower_case_spelling() -> None:
    attributes = {"p.team": "exact", "p.TEAM": "folded", "p.Team": "folded too"}
    drops = MetadataDrops()

    metadata = metadata_from_attributes(attributes, prefix="p.", drops=drops)

    assert metadata == {"team": "exact"}
    assert drops == MetadataDrops(key=2)


def test_metadata_from_attributes_keeps_the_first_32_in_lexical_order() -> None:
    attributes = {f"p.k{i:02d}": "v" for i in reversed(range(34))}
    drops = MetadataDrops()

    metadata = metadata_from_attributes(attributes, prefix="p.", drops=drops)

    assert sorted(metadata) == [f"k{i:02d}" for i in range(32)]
    assert drops == MetadataDrops(count=2)


def _dropped(path: str, reason: str) -> float:
    value = REGISTRY.get_sample_value(
        "spanlight_trace_metadata_dropped_total", {"path": path, "reason": reason}
    )
    return value or 0.0


def test_record_metadata_drops_counts_each_non_zero_reason() -> None:
    before = {reason: _dropped("otlp", reason) for reason in ("key", "value", "type", "count")}

    record_metadata_drops("otlp", MetadataDrops(key=2, count=1))

    assert _dropped("otlp", "key") == before["key"] + 2
    assert _dropped("otlp", "count") == before["count"] + 1
    assert _dropped("otlp", "value") == before["value"]
    assert _dropped("otlp", "type") == before["type"]
