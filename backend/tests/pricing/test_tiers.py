"""Service-tier spellings and tier multipliers of the cost spec (§7.2). Pure: no database.

The spelling cases live in `sdks/fixtures/service-tier-cases.json`, shared with the SDK's test so
both sides normalize a provider's `service_tier` the same way.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.pricing import tiers
from app.pricing.prices import PriceSnapshot
from app.pricing.tiers import SERVICE_TIERS, TierMultipliers, multipliers_for, normalize_tier

FIXTURE = Path(__file__).resolve().parents[3] / "sdks" / "fixtures" / "service-tier-cases.json"
CASES: list[dict[str, str | None]] = json.loads(FIXTURE.read_text())

# The table of the 2026-10-16 snapshot (plan Global Constraints, spec §7.2, as amended by the
# ruling of 2026-10-15: OpenAI priority carries no factor, see `ruling-openai-priority.md`).
TABLE: dict[tuple[str, str], Decimal] = {
    ("anthropic", "batch"): Decimal("0.5"),
    ("anthropic", "priority"): Decimal("1.0"),
    ("openai", "batch"): Decimal("0.5"),
    ("openai", "flex"): Decimal("0.5"),
    ("google", "batch"): Decimal("0.5"),
}
COMMITMENT_BILLED = frozenset({("anthropic", "priority")})

# OpenAI standard and Fast (formerly priority processing) rates in USD per million tokens,
# (input, output), short context, read from https://developers.openai.com/api/docs/pricing on
# 2026-10-15. A model missing from `OPENAI_FAST` has no row in the page's Fast table that day
# (gpt-5-nano, o3-mini). gpt-5 is the pair spec §4.4 pins; the six models from gpt-4o down are
# the OpenAI rows the price snapshot carries.
OPENAI_STANDARD = {
    "gpt-5": (Decimal("1.25"), Decimal("10.00")),
    "gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
    "gpt-5-nano": (Decimal("0.05"), Decimal("0.40")),
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4.1": (Decimal("2.00"), Decimal("8.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "o3-mini": (Decimal("1.10"), Decimal("4.40")),
    "o4-mini": (Decimal("1.10"), Decimal("4.40")),
}
OPENAI_FAST = {
    "gpt-5": (Decimal("2.50"), Decimal("20.00")),
    "gpt-5-mini": (Decimal("0.45"), Decimal("3.60")),
    "gpt-4o": (Decimal("4.25"), Decimal("17.00")),
    "gpt-4o-mini": (Decimal("0.25"), Decimal("1.00")),
    "gpt-4.1": (Decimal("3.50"), Decimal("14.00")),
    "gpt-4.1-mini": (Decimal("0.70"), Decimal("2.80")),
    "o4-mini": (Decimal("2.00"), Decimal("8.00")),
}
PUBLISHED_FAST_RATIO = {"gpt-5": Decimal("2"), "gpt-5-mini": Decimal("1.8")}
SNAPSHOT_OPENAI_MODELS = ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini", "o4-mini")

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def table() -> TierMultipliers:
    return TierMultipliers(table=TABLE, commitment_billed=COMMITMENT_BILLED)


@pytest.mark.parametrize("case", CASES, ids=[f"{c['provider']}:{c['raw']!r}" for c in CASES])
def test_normalize_tier_fixture(case: dict[str, str | None]) -> None:
    assert normalize_tier(case["provider"], case["raw"]) == case["expected"]


def test_fixture_covers_every_tier() -> None:
    assert {case["expected"] for case in CASES} - {None} == SERVICE_TIERS
    assert frozenset({"standard", "batch", "priority", "flex"}) == SERVICE_TIERS


@pytest.mark.parametrize(
    ("provider", "raw", "expected"),
    [
        (None, "batch", "batch"),
        (None, "fast", None),
        ("Claude", "PRIORITY_ON_DEMAND", "priority"),
        ("azure_openai", "scale", None),
        ("openrouter", "flex", "flex"),
        ("openrouter", "default", None),
        ("anthropic", None, None),
        ("anthropic", "   ", None),
    ],
)
def test_normalize_tier_edges(provider: str | None, raw: str | None, expected: str | None) -> None:
    assert normalize_tier(provider, raw) == expected


def test_multipliers_table(table: TierMultipliers) -> None:
    for (provider, tier), factor in TABLE.items():
        assert table.for_(provider, tier) == factor
    assert table.for_("anthropic", "standard") == 1
    assert table.for_("anthropic", None) == 1
    assert table.for_(None, "batch") == 1
    assert table.for_("openrouter", "batch") == 1
    assert table.for_("anthropic", "flex") == 1
    assert table.for_("google", "priority") == 1
    assert table.for_(" Claude ", "batch") == Decimal("0.5")
    assert isinstance(table.for_("openai", "batch"), Decimal)


def test_multipliers_note(table: TierMultipliers) -> None:
    assert table.note("anthropic", "priority") == "commitment_billed"
    assert table.note("claude", "priority") == "commitment_billed"
    assert table.note("anthropic", "batch") is None
    assert table.note("openai", "standard") is None
    assert table.note("openai", "priority") == "unmodelled"
    assert table.note("google", "priority") == "unmodelled"
    assert table.note("anthropic", "flex") == "unmodelled"
    assert table.note(None, "priority") is None
    assert table.note("openai", None) is None
    assert table.note(None, None) is None


def test_multipliers_are_frozen(table: TierMultipliers) -> None:
    with pytest.raises(AttributeError):
        table.table = {}  # type: ignore[misc]
    with pytest.raises(TypeError):
        table.table[("openai", "priority")] = Decimal(2)  # type: ignore[index]
    # Wrapping copies, so the caller's dict cannot reach in afterwards either.
    source = {("openai", "batch"): Decimal("0.5")}
    wrapped = TierMultipliers(table=source)
    source[("openai", "batch")] = Decimal(9)
    assert wrapped.for_("openai", "batch") == Decimal("0.5")


def _stub(version: str, year: int, table: dict[tuple[str, str], Decimal]) -> PriceSnapshot:
    return PriceSnapshot(
        version=version,
        effective_from=datetime(year, 1, 1, tzinfo=UTC),
        prices=(),
        tier_multipliers=table,
    )


OLD_TABLE = {("openai", "batch"): Decimal("0.5")}
NEW_TABLE = {("openai", "batch"): Decimal("0.25"), ("google", "batch"): Decimal("0.5")}


@pytest.fixture
def snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    # Newest first on purpose: resolution must go by `effective_from`, not tuple order.
    monkeypatch.setattr(
        tiers,
        "ALL_SNAPSHOTS",
        (
            _stub("new", 2026, NEW_TABLE),
            _stub("old", 2025, OLD_TABLE),
        ),
    )


@pytest.mark.usefixtures("snapshots")
def test_seed_version_names_its_snapshot_whatever_the_time() -> None:
    before_both = datetime(2024, 6, 1, tzinfo=UTC)
    assert multipliers_for("new", before_both).for_("openai", "batch") == Decimal("0.25")
    assert multipliers_for("old", AT).for_("openai", "batch") == Decimal("0.5")
    assert multipliers_for("old", AT).for_("google", "batch") == 1


@pytest.mark.usefixtures("snapshots")
def test_override_or_unknown_version_uses_the_snapshot_in_force() -> None:
    during_old = datetime(2025, 6, 1, tzinfo=UTC)
    for version in ("override:7", "2001-01-01", None):
        assert multipliers_for(version, AT).for_("openai", "batch") == Decimal("0.25")
        assert multipliers_for(version, during_old).for_("openai", "batch") == Decimal("0.5")
    # Exactly at `effective_from` the new snapshot is already in force.
    boundary = datetime(2026, 1, 1, tzinfo=UTC)
    assert multipliers_for(None, boundary).for_("openai", "batch") == Decimal("0.25")


@pytest.mark.usefixtures("snapshots")
def test_before_every_snapshot_every_factor_is_one() -> None:
    before_both = datetime(2024, 6, 1, tzinfo=UTC)
    table = multipliers_for(None, before_both)
    assert table.for_("openai", "batch") == 1
    assert table.note("openai", "batch") == "unmodelled"
    assert not table.table


def test_snapshot_without_multipliers_uses_the_oldest_modelled_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A snapshot released before tiers were modelled carries no table of its own; the factors
    # were provider facts on its dates too, so it resolves to the oldest table any snapshot
    # carries (not the newest: a later change of a factor must not reach back).
    bare = PriceSnapshot(version="bare", effective_from=datetime(2025, 1, 1, tzinfo=UTC), prices=())
    monkeypatch.setattr(
        tiers,
        "ALL_SNAPSHOTS",
        (_stub("new", 2027, NEW_TABLE), bare, _stub("old", 2026, OLD_TABLE)),
    )
    for version, at in (("bare", AT), (None, datetime(2025, 6, 1, tzinfo=UTC))):
        table = multipliers_for(version, at)
        assert table.table == OLD_TABLE
        assert table.for_("openai", "batch") == Decimal("0.5")
        assert table.note("google", "batch") == "unmodelled"


def test_snapshot_without_multipliers_is_empty_when_no_snapshot_models_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare = PriceSnapshot(version="bare", effective_from=datetime(2025, 1, 1, tzinfo=UTC), prices=())
    monkeypatch.setattr(tiers, "ALL_SNAPSHOTS", (bare,))
    table = multipliers_for("bare", AT)
    assert not table.table
    assert table.for_("openai", "batch") == 1
    assert table.note("anthropic", "priority") == "unmodelled"


@pytest.mark.parametrize("model", sorted(PUBLISHED_FAST_RATIO))
def test_openai_priority_matches_published_fast_rates(model: str) -> None:
    standard_input, standard_output = OPENAI_STANDARD[model]
    fast_input, fast_output = OPENAI_FAST[model]
    ratio = PUBLISHED_FAST_RATIO[model]
    assert standard_input * ratio == fast_input
    assert standard_output * ratio == fast_output


def test_openai_priority_is_unmodelled(table: TierMultipliers) -> None:
    assert table.for_("openai", "priority") == 1
    assert table.note("openai", "priority") == "unmodelled"


def test_no_single_factor_reproduces_openai_fast_rates() -> None:
    priced = [m for m in SNAPSHOT_OPENAI_MODELS if m in OPENAI_FAST]
    assert "o3-mini" not in priced
    input_ratios = {OPENAI_FAST[m][0] / OPENAI_STANDARD[m][0] for m in priced}
    output_ratios = {OPENAI_FAST[m][1] / OPENAI_STANDARD[m][1] for m in priced}
    assert len(input_ratios) > 1
    assert Decimal(2) not in input_ratios
    assert Decimal(2) not in output_ratios
