"""Service tiers and their price multipliers. Pure: no I/O.

Providers spell the same tier many ways (`priority_on_demand`, `fast`, `scale`), while the
`spans.service_tier` column holds exactly four values or NULL. `normalize_tier` folds a provider's
spelling onto those values; an unknown spelling is `None` and the span is priced as standard,
the raw value being kept by the caller in `attributes["usage.service_tier_raw"]`.

A snapshot carries its own multiplier table, so a span priced under an older snapshot keeps the
factor that was in force then. A non-standard tier the snapshot carries no factor for (OpenAI
priority, whose Fast rates are published per model, not as a multiplier) is priced at the
standard rate and flagged `unmodelled`, never silently. A snapshot released before tiers were
modelled (2026-10-01, 2026-10-15) carries no table of its own and is never edited; the factors
are provider facts that predate those releases (batch and flex were half price then too), so a
span priced under it takes the table of the oldest snapshot that carries one. The oldest, not
the newest: a later change of a factor must not reach back to spans it never applied to.

A snapshot's modifier factors (`modifier_multipliers`, data residency and fast mode) multiply the
tier's. They come from the snapshot itself, never from an older one: a snapshot without them
priced every modifier at the standard rate. OpenAI `ultrafast` has no stored tier (the column
holds four values), so it normalizes to `None`, keeps its spelling in `usage.service_tier_raw`
and is counted as unmodelled by the cost rollups.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from app.pricing.prices import ALL_SNAPSHOTS, PriceSnapshot
from app.pricing.provider_alias import canonical_provider

ServiceTier = Literal["standard", "batch", "priority", "flex"]
SERVICE_TIERS: frozenset[str] = frozenset({"standard", "batch", "priority", "flex"})
TierNote = Literal["commitment_billed", "unmodelled"]
# The longest unknown tier spelling a span keeps; a longer one is cut (the gateway, OTLP, the SDK).
MAX_RAW_TIER_LENGTH = 64

_ONE = Decimal(1)

# A canonical tier name is accepted as is from every provider: the batch recorders set `batch`
# whatever the provider, and the native API accepts only these four.
_CANONICAL: dict[str, ServiceTier] = {
    "standard": "standard",
    "batch": "batch",
    "priority": "priority",
    "flex": "flex",
}
# Provider spellings per spec §7.2, after the canonical names.
_PROVIDER_SPELLINGS: dict[str, dict[str, ServiceTier]] = {
    "anthropic": {"priority_on_demand": "priority", "flex_discount": "flex"},
    "openai": {"default": "standard", "auto": "standard", "scale": "standard", "fast": "priority"},
}


def normalize_tier(provider: str | None, raw: str | None) -> ServiceTier | None:
    """The stored tier for a provider's `service_tier` value, or `None` when it is unknown.

    The value is trimmed and lower-cased first, so `"  Batch "` and `"FLEX_DISCOUNT"` normalize.
    The provider goes through `canonical_provider`, so a Vertex or Claude spelling reads the same
    table as the seeded name; Azure is not an OpenAI alias, so its spellings stay unknown. With no
    provider only the four canonical names are recognised.
    """
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    canonical = _CANONICAL.get(cleaned)
    if canonical is not None:
        return canonical
    provider_name = canonical_provider(provider)
    if provider_name is None:
        return None
    return _PROVIDER_SPELLINGS.get(provider_name, {}).get(cleaned)


def unknown_tier_spelling(raw: str | None, tier: ServiceTier | None) -> str | None:
    """The spelling to keep in `attributes["usage.service_tier_raw"]`: `raw` trimmed when it did
    not normalize (`tier` is None), cut to `MAX_RAW_TIER_LENGTH`; None when it normalized or is
    blank. The cut bounds what one upstream (a custom base URL) can add to a span's attributes."""
    if tier is not None or raw is None:
        return None
    return raw.strip()[:MAX_RAW_TIER_LENGTH] or None


def _empty_table() -> Mapping[tuple[str, str], Decimal]:
    return MappingProxyType({})


@dataclass(frozen=True)
class TierMultipliers:
    """The (provider, tier) → multiplier table of one price snapshot.

    Keys are canonical provider names and stored tier values. A pair not in the table is billed
    at the standard rate, as are `standard`, an unknown tier and a missing provider. The table is
    wrapped read-only on construction, so neither the dataclass nor its mapping can be mutated.
    """

    table: Mapping[tuple[str, str], Decimal] = field(default_factory=_empty_table)
    commitment_billed: frozenset[tuple[str, str]] = frozenset()
    modifiers: Mapping[tuple[str, str], Decimal] = field(default_factory=_empty_table)

    def __post_init__(self) -> None:
        object.__setattr__(self, "table", MappingProxyType(dict(self.table)))
        object.__setattr__(self, "modifiers", MappingProxyType(dict(self.modifiers)))

    def modifier(self, provider: str | None, name: str) -> Decimal | None:
        """The factor of a pricing modifier (`inference_geo_us`, `fast`), or None when the
        snapshot has none: the modifier is then priced at the standard rate and flagged."""
        return self.modifiers.get((canonical_provider(provider) or "", name))

    def factor(
        self, provider: str | None, tier: str | None, modifiers: Iterable[str] = ()
    ) -> Decimal:
        """The tier's factor times each modifier's; a modifier without one counts as 1."""
        result = self.for_(provider, tier)
        for name in modifiers:
            result *= self.modifier(provider, name) or _ONE
        return result

    def for_(self, provider: str | None, tier: str | None) -> Decimal:
        """The factor applied to the standard token cost; 1 whenever the table has no row."""
        key = _key(provider, tier)
        if key is None:
            return _ONE
        return self.table.get(key, _ONE)

    def note(self, provider: str | None, tier: str | None) -> TierNote | None:
        """Why the factor is what it is, when that needs saying.

        `"commitment_billed"` for a tier the provider bills outside the per-token rate;
        `"unmodelled"` for a non-standard tier the snapshot carries no factor for, which is priced
        at the standard rate and flagged, never silently; `None` for standard, a missing tier or a
        missing provider.
        """
        key = _key(provider, tier)
        if key is None:
            return None
        if key in self.commitment_billed:
            return "commitment_billed"
        if key not in self.table:
            return "unmodelled"
        return None


def _key(provider: str | None, tier: str | None) -> tuple[str, str] | None:
    provider_name = canonical_provider(provider)
    if provider_name is None or tier is None or tier == "standard":
        return None
    return provider_name, tier


def multipliers_for(version: str | None, at: datetime) -> TierMultipliers:
    """The multiplier table of the snapshot named `version`, or of the one in force at `at`.

    A seed version names its snapshot. An override version (`override:<id>`) or an unknown one
    uses the snapshot whose `effective_from` is the latest not after `at`; before every snapshot
    nothing is in force and every factor is 1. A snapshot without a table of its own resolves
    to the oldest table any snapshot carries (module docstring).
    """
    snapshot = _by_version(version) or _in_force(at)
    if snapshot is None:
        return TierMultipliers()
    modifiers = snapshot.modifier_multipliers
    if not snapshot.tier_multipliers:
        snapshot = _oldest_modelled() or snapshot
    return TierMultipliers(
        table=snapshot.tier_multipliers,
        commitment_billed=snapshot.commitment_billed,
        modifiers=modifiers,
    )


def _oldest_modelled() -> PriceSnapshot | None:
    modelled = [s for s in ALL_SNAPSHOTS if s.tier_multipliers]
    return min(modelled, key=lambda s: s.effective_from) if modelled else None


def _by_version(version: str | None) -> PriceSnapshot | None:
    if version is None:
        return None
    return next((s for s in ALL_SNAPSHOTS if s.version == version), None)


def _in_force(at: datetime) -> PriceSnapshot | None:
    in_force = [s for s in ALL_SNAPSHOTS if s.effective_from <= at]
    return max(in_force, key=lambda s: s.effective_from) if in_force else None
