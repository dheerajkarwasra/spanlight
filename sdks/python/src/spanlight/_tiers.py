"""Service-tier normalization: the SDK's copy of the server's table.

``normalize_tier`` is a verbatim copy of ``backend/app/pricing/tiers.py::normalize_tier``,
including the provider alias sets of ``backend/app/pricing/provider_alias.py`` that it reads
through. The SDK cannot import the backend, so the two copies are kept in step by one shared
fixture, ``sdks/fixtures/service-tier-cases.json``, that both test suites run. Change the
backend table first, then this one, then the fixture.

Providers spell the same tier many ways (``priority_on_demand``, ``fast``, ``scale``), while the
server stores exactly four values or ``NULL``. An unknown spelling is ``None``: the span is
priced as standard and :meth:`Span.set_usage` keeps the raw value in
``attributes["usage.service_tier_raw"]``.
"""

from __future__ import annotations

from typing import Literal

ServiceTier = Literal["standard", "batch", "priority", "flex"]

# The longest unknown tier spelling a span keeps in ``usage.service_tier_raw``; the server cuts
# a longer one to the same length, so the SDK sends no more.
MAX_RAW_TIER_LENGTH = 64

# Provider aliases, copied from the backend's `provider_alias.py`.
_GOOGLE_ALIASES: frozenset[str] = frozenset(
    {
        "gemini",
        "google",
        "google_genai",
        "google-genai",
        "google_ai",
        "vertex_ai",
        "vertexai",
        "gcp.gemini",
        "gcp.vertex_ai",
        "gcp.gen_ai",
    }
)
_ANTHROPIC_ALIASES: frozenset[str] = frozenset({"anthropic", "claude"})
# Azure is not an OpenAI alias on the server (it bills its own rates), so it is not one here.
_OPENAI_ALIASES: frozenset[str] = frozenset({"openai"})

_CANONICAL_PROVIDER: dict[str, str] = {
    **dict.fromkeys(_GOOGLE_ALIASES, "google"),
    **dict.fromkeys(_ANTHROPIC_ALIASES, "anthropic"),
    **dict.fromkeys(_OPENAI_ALIASES, "openai"),
}

# A canonical tier name is accepted as is from every provider: the batch recorders set `batch`
# whatever the provider, and the native API accepts only these four.
_CANONICAL_TIER: dict[str, ServiceTier] = {
    "standard": "standard",
    "batch": "batch",
    "priority": "priority",
    "flex": "flex",
}
# Provider spellings per the Bet 1 spec §7.2, after the canonical names.
_PROVIDER_SPELLINGS: dict[str, dict[str, ServiceTier]] = {
    "anthropic": {"priority_on_demand": "priority", "flex_discount": "flex"},
    "openai": {"default": "standard", "auto": "standard", "scale": "standard", "fast": "priority"},
}


def _canonical_provider(name: str | None) -> str | None:
    """The server's provider name for ``name``: trimmed, lower-cased, then aliased."""
    if name is None:
        return None
    cleaned = name.strip().lower()
    if not cleaned:
        return None
    return _CANONICAL_PROVIDER.get(cleaned, cleaned)


def normalize_tier(provider: str | None, raw: str | None) -> ServiceTier | None:
    """The stored tier for a provider's ``service_tier`` value, or ``None`` when it is unknown.

    The value is trimmed and lower-cased first, so ``"  Batch "`` and ``"FLEX_DISCOUNT"``
    normalize. The provider is aliased the way the server does it, so a Vertex or Claude spelling
    reads the same table as the canonical name; an Azure spelling is not folded onto OpenAI. With
    no provider only the four canonical names are recognised.
    """
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    canonical = _CANONICAL_TIER.get(cleaned)
    if canonical is not None:
        return canonical
    provider_name = _canonical_provider(provider)
    if provider_name is None:
        return None
    return _PROVIDER_SPELLINGS.get(provider_name, {}).get(cleaned)
