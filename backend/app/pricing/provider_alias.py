"""Canonical provider names for price lookup. Pure: no I/O.

Integrations spell the same provider many ways (`gemini`, `vertex_ai`, `gcp.gen_ai`), while the
price table seeds one name per provider. `canonical_provider` folds the known spellings onto the
seeded name so a Gemini span sent through Vertex still finds its price. The price and fee lookups
fold both sides, the span's provider and each row's, so an override saved as `gemini` matches a
span sent as `google` and the other way round. Only the lookups and the rollup's
`provider_canonical` use it; the stored `spans.provider` never changes, and filters compare what
was stored.
"""

GOOGLE_ALIASES: frozenset[str] = frozenset(
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
ANTHROPIC_ALIASES: frozenset[str] = frozenset({"anthropic", "claude"})
# Azure bills its own rates. Preserve the two spellings for existing overrides.
AZURE_PROVIDER_NAMES: frozenset[str] = frozenset({"azure", "azure_openai"})
OPENAI_ALIASES: frozenset[str] = frozenset({"openai"})

_CANONICAL: dict[str, str] = {
    **dict.fromkeys(GOOGLE_ALIASES, "google"),
    **dict.fromkeys(ANTHROPIC_ALIASES, "anthropic"),
    **dict.fromkeys(OPENAI_ALIASES, "openai"),
    "mistralai": "mistral",
    "grok": "xai",
    "together_ai": "together",
    "togetherai": "together",
    "aws_bedrock": "bedrock",
    "amazon_bedrock": "bedrock",
}


def canonical_provider(name: str | None) -> str | None:
    """The seeded provider name for `name`: trimmed, lower-cased, then aliased.

    An unknown spelling comes back trimmed and lower-cased; a missing or blank name is `None`.
    """
    if name is None:
        return None
    cleaned = name.strip().lower()
    if not cleaned:
        return None
    return _CANONICAL.get(cleaned, cleaned)
