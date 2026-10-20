"""Render the recorded OTLP fixtures as the compatibility page of the documentation site.

Run from `backend/` after recording, re-recording or removing a fixture:

    uv run python scripts/gen_otlp_matrix.py            # rewrite the page
    uv run python scripts/gen_otlp_matrix.py --check    # exit 1 if the page is out of date

Every `sdks/fixtures/otlp/<library>/<version>.json` (format: `sdks/fixtures/otlp/schema.json`)
becomes one table with a row per recorded scenario. A cell is "captured" when a span of the
scenario's `expected` rows holds a value for the field; otherwise "not sent by the exporter" when
the fixture's `loses` names it, "not read" when `NOT_READ` lists it as a documented non-goal for
the library, and "not captured" in any other case. A null field is never shown as captured. The
whole page is generated: it is never edited by hand. The output depends on the fixtures only, so
two runs over the same fixtures write the same bytes.
"""

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = REPO_ROOT / "sdks" / "fixtures" / "otlp"
PAGE_PATH = (
    REPO_ROOT / "docs-site" / "src" / "content" / "docs" / "reference" / "otlp-compatibility.mdx"
)
UPDATE_COMMAND = "cd backend && uv run python scripts/gen_otlp_matrix.py"
GITHUB = "https://github.com/dheerajkarwasra/spanlight"
WEEKLY_URL = f"{GITHUB}/actions/workflows/otlp-matrix-weekly.yml"

CAPTURED = "captured"
NOT_SENT = "not sent by the exporter"
NOT_READ_CELL = "not read"
NOT_CAPTURED = "not captured"

Span = dict[str, Any]


def _usage(key: str) -> Callable[[Span], Any]:
    return lambda span: span["usage"][key]


def _trace_fields(span: Span) -> Any:
    fields = ("name", "environment", "release", "user_id", "session_id")
    return next((span["trace"][f] for f in fields if span["trace"][f] is not None), None)


# Each column of the tables: the names in a fixture's `loses` that mark it as not sent, and how
# to read its stored value from an `expected` span (None, False or empty means no value).
COLUMNS: tuple[tuple[str, frozenset[str], Callable[[Span], Any]], ...] = (
    ("Provider", frozenset({"provider"}), lambda span: span["provider"]),
    ("Model", frozenset({"model"}), lambda span: span["model"]),
    ("Input tokens", frozenset({"usage", "input_tokens"}), _usage("input_tokens")),
    ("Output tokens", frozenset({"usage", "output_tokens"}), _usage("output_tokens")),
    ("Cached", frozenset({"usage", "cached_tokens"}), _usage("cached_tokens")),
    (
        "Cache writes",
        frozenset({"usage", "cache_write_tokens", "cache_write_1h_tokens"}),
        _usage("cache_write_tokens"),
    ),
    ("Prompt", frozenset({"input"}), lambda span: span["input_present"] or None),
    ("Completion", frozenset({"output"}), lambda span: span["output_present"] or None),
    ("Finish reason", frozenset({"finish_reason"}), lambda span: span["finish_reason"]),
    ("TTFT", frozenset({"time_to_first_token_ms"}), lambda span: span["time_to_first_token_ms"]),
    ("Kind", frozenset({"kind"}), lambda span: span["kind"]),
    ("Error class", frozenset({"error_class"}), lambda span: span["error_class"]),
    (
        "Trace fields",
        frozenset({"trace", "name", "environment", "release", "user_id", "session_id"}),
        _trace_fields,
    ),
    ("Metadata", frozenset({"metadata"}), lambda span: span["trace"]["metadata"] or None),
    ("Usage rollup", frozenset({"usage_rollup"}), lambda span: span["usage_rollup"]),
)
KNOWN_LOSES = frozenset().union(*(names for _, names, _ in COLUMNS))

# Library -> columns Spanlight deliberately does not read for it (a non-goal of the OTLP path).
# OpenInference reports the first token as a span event, and span events are not read.
NOT_READ: dict[str, frozenset[str]] = {
    "openinference-langchain": frozenset({"TTFT"}),
    "openinference-openai": frozenset({"TTFT"}),
}

# Versions land in a heading of an MDX page, where `{` or `<` would break the build.
SAFE_VERSION = re.compile(r"^[A-Za-z0-9.+_-]+$")


def load_fixtures(root: Path) -> list[dict[str, Any]]:
    """Every fixture under `root`, sorted by library, then by version in numeric order."""
    fixtures: list[dict[str, Any]] = []
    for path in root.glob("*/*.json"):
        if path.parent.name == "record":
            continue
        fixture = json.loads(path.read_text(encoding="utf-8"))
        _validate(path, fixture)
        fixtures.append(fixture)
    return sorted(fixtures, key=lambda f: (f["library"], _natural(f["version"])))


def _natural(version: str) -> list[int | str]:
    # re.split with a group alternates text and digits, so positions never mix types.
    return [int(p) if i % 2 else p for i, p in enumerate(re.split(r"(\d+)", version))]


def _validate(path: Path, fixture: dict[str, Any]) -> None:
    where = path.relative_to(REPO_ROOT)
    if (fixture.get("library"), fixture.get("version")) != (path.parent.name, path.stem):
        raise SystemExit(f"gen_otlp_matrix: {where} does not match its library/version path")
    if not SAFE_VERSION.match(fixture["version"]):
        raise SystemExit(f"gen_otlp_matrix: {where} has an unsafe version {fixture['version']!r}")
    unknown_columns = NOT_READ.get(fixture["library"], frozenset()) - {c for c, _, _ in COLUMNS}
    if unknown_columns:
        raise SystemExit(f"gen_otlp_matrix: NOT_READ names unknown columns {unknown_columns}")
    for scenario in fixture["scenarios"]:
        unknown = set(scenario["expected"]["loses"]) - KNOWN_LOSES
        if unknown:
            raise SystemExit(
                f"gen_otlp_matrix: {where} scenario {scenario['name']} loses unknown fields "
                f"{sorted(unknown)}; map them to a column in COLUMNS"
            )


def cell(
    library: str,
    column: tuple[str, frozenset[str], Callable[[Span], Any]],
    expected: dict[str, Any],
) -> str:
    header, not_sent, value = column
    if any(value(span) is not None for span in expected["spans"]):
        return CAPTURED
    if not_sent & set(expected["loses"]):
        return NOT_SENT
    if header in NOT_READ.get(library, frozenset()):
        return NOT_READ_CELL
    return NOT_CAPTURED


def render_table(fixture: dict[str, Any]) -> str:
    library, version = fixture["library"], fixture["version"]
    recorder = fixture["recorder"]
    lines = [
        f"### {library} {version}",
        "",
        f"Recorded {fixture['recorded_at']} on {recorder['runtime']} with OpenTelemetry SDK "
        f"{recorder['otel_sdk']} (harness `{recorder['harness']}`). "
        f"Fixture: `sdks/fixtures/otlp/{library}/{version}.json`.",
        "",
        "| Scenario | " + " | ".join(column for column, _, _ in COLUMNS) + " |",
        "| --- " * (len(COLUMNS) + 1) + "|",
    ]
    for scenario in sorted(fixture["scenarios"], key=lambda s: s["name"]):
        cells = [cell(library, column, scenario["expected"]) for column in COLUMNS]
        lines.append(f"| `{scenario['name']}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


DESCRIPTION = (
    "What each OpenTelemetry exporter library sends to Spanlight, scenario by scenario, generated "
    "from the recorded fixtures that the contract test replays on every change."
)
INTRO = (
    "This page lists, for each exporter library and pinned version, which Spanlight fields a span "
    "sent through [OTLP ingestion](/docs/otlp/) fills. Supported means a green fixture: each table "
    "below comes from a fixture that holds the exact bytes the library exported for each scenario "
    "and the rows Spanlight is expected to store for them. The contract test posts those bytes on "
    "every change and checks every field, so the tables cannot drift from what the code does. The "
    f"[`otlp-matrix-weekly`]({WEEKLY_URL}) workflow re-records every library at its latest release "
    "each week and reports any breakage."
)
LEGEND = (
    f"- **{CAPTURED}**: Spanlight stores the field from what the library sends.\n"
    f"- **{NOT_SENT}**: the library does not send the field in this scenario, so it stays empty "
    '("—" in the app).\n'
    f"- **{NOT_READ_CELL}**: the library sends the field in a form Spanlight does not read, a "
    "documented non-goal of the OTLP path (OpenInference sends the time to first token as a span "
    "event, and span events are not read).\n"
    f"- **{NOT_CAPTURED}**: the scenario stores no value for the field, for example no finish "
    "reason on a request that failed."
)
HEADER = f"""---
title: OTLP compatibility
description: {DESCRIPTION}
editUrl: "{GITHUB}/edit/main/backend/scripts/gen_otlp_matrix.py"
---

{{/* Sources: sdks/fixtures/otlp/schema.json, backend/scripts/gen_otlp_matrix.py */}}

{{/* Generated from sdks/fixtures/otlp. Do not edit by hand: run {UPDATE_COMMAND} */}}

{INTRO}

Each cell says one of four things:

{LEGEND}

For which attribute fills which field, see [OTLP attribute mapping](/docs/reference/otlp-mapping/).

## Recorded libraries
"""

FOOTER = """## Related pages

- [OTLP ingestion](/docs/otlp/): send traces from any OpenTelemetry exporter over OTLP/HTTP.
- [OTLP attribute mapping](/docs/reference/otlp-mapping/): which attribute fills which field.
"""


def render_page(fixtures: list[dict[str, Any]]) -> str:
    tables = [render_table(fixture) for fixture in fixtures] or ["No fixture is recorded yet.\n"]
    return "\n".join([HEADER, *tables, FOOTER])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the page is out of date")
    arguments = parser.parse_args()

    updated = render_page(load_fixtures(FIXTURES_ROOT))
    current = PAGE_PATH.read_text(encoding="utf-8") if PAGE_PATH.exists() else None
    if arguments.check:
        if updated != current:
            print(
                f"{PAGE_PATH.name} is out of date with the OTLP fixtures. Run: {UPDATE_COMMAND}",
                file=sys.stderr,
            )
            return 1
        print(f"{PAGE_PATH.name} is up to date")
        return 0
    if updated != current:
        PAGE_PATH.write_text(updated, encoding="utf-8")
        print(f"wrote {PAGE_PATH}")
    else:
        print(f"{PAGE_PATH.name} already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
