"""`spanlight prices diff`: compare the bundled price snapshot with LiteLLM's public price file.

A review aid, never a source of prices: it reports rates that differ and models the feed knows
that the snapshot does not, with a proposed `_price(...)` line for each. A human checks every
figure against the provider's own pricing page before a new snapshot is written; nothing here
writes to `prices.py` or the database.
"""

import json
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from app.core.model_match import matches_model
from app.pricing.prices import PRICING_VERSION, SEED_PRICES, SeedPrice

prices_app = typer.Typer(help="The bundled price table.", no_args_is_help=True)

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
# Spanlight provider -> LiteLLM `litellm_provider` and the key prefix its models carry.
PROVIDERS: Mapping[str, tuple[str, str]] = {
    "openai": ("openai", ""),
    "anthropic": ("anthropic", ""),
    "google": ("gemini", "gemini/"),
}
# Seed field -> LiteLLM per-token field.
FIELDS: Mapping[str, str] = {
    "input_per_mtok": "input_cost_per_token",
    "output_per_mtok": "output_cost_per_token",
    "cached_input_per_mtok": "cache_read_input_token_cost",
    "cache_write_5m_per_mtok": "cache_creation_input_token_cost",
    "cache_write_1h_per_mtok": "cache_creation_input_token_cost_above_1hr",
}
CHAT_MODES = {"chat", "responses"}


def _per_mtok(value: Any) -> Decimal | None:
    if not isinstance(value, int | float):
        return None
    # Fixed-point text: `normalize()` alone would print 20 as `2E+1`.
    return Decimal(format(round(Decimal(str(value)) * 1_000_000, 6).normalize(), "f"))


def _feed_rate(entry: Mapping[str, Any], field: str, min_prompt_tokens: int) -> Decimal | None:
    suffix = f"_above_{(min_prompt_tokens - 1) // 1000}k_tokens" if min_prompt_tokens else ""
    return _per_mtok(entry.get(FIELDS[field] + suffix))


def rate_differences(
    seeds: Iterable[SeedPrice], feed: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    """`(lines for rates that differ, patterns the feed does not have)`.

    A rate the feed leaves out is not a difference: the feed is incomplete, not authoritative.
    """
    differences: list[str] = []
    absent: list[str] = []
    for seed in seeds:
        _, prefix = PROVIDERS.get(seed.provider, (seed.provider, ""))
        entry = feed.get(prefix + seed.model_pattern) or feed.get(seed.model_pattern)
        if entry is None:  # the feed may list only dated snapshots of the model
            dated = sorted(key for key in feed if matches_model(seed.model_pattern, key))
            entry = feed[dated[-1]] if dated else None
        if not isinstance(entry, dict):
            absent.append(f"{seed.provider} {seed.model_pattern}")
            continue
        for field in FIELDS:
            ours: Decimal | None = getattr(seed, field)
            theirs = _feed_rate(entry, field, seed.min_prompt_tokens)
            if theirs is None or (ours is not None and ours == theirs):
                continue
            tier = f" (over {seed.min_prompt_tokens - 1:,})" if seed.min_prompt_tokens else ""
            shown = "none" if ours is None else format(ours.normalize(), "f")
            differences.append(
                f"{seed.provider} {seed.model_pattern}{tier} {field}: ours {shown}, feed {theirs}"
            )
    return differences, sorted(set(absent))


def missing_models(seeds: Iterable[SeedPrice], feed: Mapping[str, Any]) -> list[str]:
    """A proposed `_price(...)` line for each chat model the feed prices and no seed row matches."""
    rows = list(seeds)
    lines: list[str] = []
    for provider, (litellm_provider, prefix) in PROVIDERS.items():
        patterns = [row.model_pattern for row in rows if row.provider == provider]
        for key, entry in sorted(feed.items()):
            if not isinstance(entry, dict) or entry.get("litellm_provider") != litellm_provider:
                continue
            if entry.get("mode") not in CHAT_MODES or not key.startswith(prefix):
                continue
            model = key.removeprefix(prefix)
            if "/" in model or model.startswith("ft:"):
                continue
            if any(matches_model(pattern, model) for pattern in patterns):
                continue
            rates = [_per_mtok(entry.get(FIELDS[field])) for field in FIELDS]
            if not rates[0] or not rates[1]:  # unpriced or free (experimental) in the feed
                continue
            args = [f'"{provider}"', f'"{model}"', f'"{rates[0]}"', f'"{rates[1]}"']
            args.append("None" if rates[2] is None else f'"{rates[2]}"')
            if rates[3] is not None:
                args.append(f'write_5m="{rates[3]}"')
            if rates[4] is not None:
                args.append(f'write_1h="{rates[4]}"')
            lines.append(f"_price({', '.join(args)}),")
    return lines


def _load(source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")):
        response = httpx.get(source, timeout=30, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
    else:
        data = json.loads(Path(source).read_text())
    if not isinstance(data, dict):
        raise typer.BadParameter("the price file is not a JSON object")
    return data


@prices_app.command("diff")
def diff(
    source: Annotated[
        str, typer.Option(help="URL or path of a LiteLLM model_prices_and_context_window.json.")
    ] = LITELLM_URL,
) -> None:
    """Compare the current snapshot with LiteLLM's price file. Exits 1 when anything differs.

    Every reported figure must be checked against the provider's pricing page before it goes
    into a new snapshot.
    """
    feed = _load(source)
    differences, absent = rate_differences(SEED_PRICES, feed)
    missing = missing_models(SEED_PRICES, feed)
    typer.echo(f"Snapshot {PRICING_VERSION} against {source}")
    typer.echo(f"\nRates that differ ({len(differences)}):")
    for line in differences:
        typer.echo(f"  {line}")
    typer.echo(f"\nPriced here, not in the feed ({len(absent)}):")
    for line in absent:
        typer.echo(f"  {line}")
    typer.echo(f"\nIn the feed, not priced here ({len(missing)}); check each before adding:")
    for line in missing:
        typer.echo(f"  {line}")
    if differences or missing:
        raise typer.Exit(1)
