"""Trace metadata carried by OTLP `spanlight.metadata.<key>` attributes (spec §4.5, §4.6).

The attributes are read on any span, not only the root, and on the resource (a service-wide
`spanlight.metadata.team`); the span wins per key. The 32-key cap applies after that merge, in
code-point key order, so a span's keys and its resource's keys compete on the same terms. The
attributes are trace-level: the mapper removes them from the span's stored `attributes`.

Lenient, like the rest of the OTLP mapping: an invalid entry is dropped and counted in `drops`,
never the span. Counts only; a dropped key or value is never logged.
"""

from collections.abc import Mapping
from typing import Any

from app.core.text import clean_json
from app.ingest.metadata import (
    MAX_METADATA_KEYS,
    MetadataDrops,
    clean_metadata,
    metadata_from_attributes,
)

METADATA_PREFIX = "spanlight.metadata."


def is_metadata_attribute(name: str) -> bool:
    return name.startswith(METADATA_PREFIX)


def trace_metadata(
    attributes: Mapping[str, Any],
    resource: Mapping[str, Any],
    drops: MetadataDrops,
    *,
    convention: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """The span's trace metadata, capped: `convention` pairs under the resource's under the span's.

    `convention` holds the raw pairs a convention mapper collected behind its own prefixes
    (`ai.telemetry.metadata.*`, OpenInference `metadata`, ...), keys already lower-cased; they
    are cleaned leniently and lose to `spanlight.metadata.*` per key (spec §7).
    """
    merged = _convention_metadata(convention, drops) if convention else {}
    merged.update(_metadata(resource, drops))
    merged.update(_metadata(attributes, drops))
    kept = sorted(merged)[:MAX_METADATA_KEYS]
    drops.add("count", len(merged) - len(kept))
    return {key: merged[key] for key in kept}


def _convention_metadata(pairs: Mapping[str, object], drops: MetadataDrops) -> dict[str, str]:
    cleaned, found = clean_metadata(clean_json(dict(pairs)), strict=False)
    drops.absorb(found)
    return cleaned


def _metadata(attributes: Mapping[str, Any], drops: MetadataDrops) -> dict[str, str]:
    # Cleaned before validation: a protobuf string may hold NUL, and a value that is only NUL
    # must be dropped here as empty rather than reach the native schema as "".
    prefixed = clean_json({k: v for k, v in attributes.items() if is_metadata_attribute(k)})
    return metadata_from_attributes(prefixed, prefix=METADATA_PREFIX, drops=drops)
