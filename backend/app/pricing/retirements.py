"""Announced model retirement dates (first-party APIs). Pure: no I/O.

A hand-maintained, current-only table, refreshed with the prices (`app.pricing.prices`). It is
kept out of the price snapshots on purpose: a stored cost must stay reproducible, so snapshots
are immutable, while a retirement date has no history and a provider can revise it.

Only announced dates are stored: Anthropic's "Deprecated" rows and OpenAI's shutdown dates.
Anthropic's "Not sooner than" dates on active models are not retirement dates and never go in.
Each published ID is its own row, the alias and the dated snapshot alike, so a snapshot with its
own date keeps it. Rows already past their date stay while the provider's page lists them:
calls to them fail, which is still worth flagging.

Matching (`retirement_for`): the span's provider folds through `canonical_provider` and only the
first-party name matches, because Amazon Bedrock and Google Cloud set their own schedules. The
model matches by `matches_model` (exact, or a `-YYYYMMDD` / `-YYYY-MM-DD` snapshot suffix) and
the longest pattern wins. A model ending in `-latest` floats to newer snapshots, so it matches
only a row published under that exact name.

Sources (checked 2026-10-25): https://platform.claude.com/docs/en/about-claude/model-deprecations
and https://developers.openai.com/api/docs/deprecations.
"""

from dataclasses import dataclass
from datetime import date

from app.core.model_match import matches_model
from app.pricing.provider_alias import canonical_provider

ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/model-deprecations"
OPENAI_SOURCE = "https://developers.openai.com/api/docs/deprecations"


@dataclass(frozen=True, slots=True)
class Retirement:
    provider: str  # canonical: "anthropic" or "openai"
    model_pattern: str  # as the provider publishes it
    retires_on: date
    replacement: str
    source: str


def _anthropic(model: str, retires_on: date, replacement: str) -> Retirement:
    return Retirement("anthropic", model, retires_on, replacement, ANTHROPIC_SOURCE)


def _openai(models: tuple[str, ...], retires_on: date, replacement: str) -> list[Retirement]:
    return [Retirement("openai", model, retires_on, replacement, OPENAI_SOURCE) for model in models]


_IMAGE_2_5 = "gpt-image-2.5-sunburst or gpt-image-2.5-flare"
_TRANSCRIBE = "gpt-live-transcribe or gpt-transcribe"
_SOL_PRO = "gpt-5.6-sol (reasoning.mode: pro)"

RETIREMENTS: tuple[Retirement, ...] = (
    _anthropic("claude-sonnet-4-5-20250929", date(2026, 11, 30), "claude-sonnet-5-5"),
    _anthropic("claude-opus-4-1-20250805", date(2026, 8, 5), "claude-opus-4-8"),
    _anthropic("claude-opus-4-20250514", date(2026, 6, 15), "claude-opus-4-8"),
    _anthropic("claude-sonnet-4-20250514", date(2026, 6, 15), "claude-sonnet-5-5"),
    _anthropic("claude-3-haiku-20240307", date(2026, 4, 20), "claude-haiku-4-5-20251001"),
    _anthropic("claude-3-7-sonnet-20250219", date(2026, 2, 19), "claude-sonnet-5-5"),
    _anthropic("claude-3-5-haiku-20241022", date(2026, 2, 19), "claude-haiku-4-5-20251001"),
    *_openai(("gpt-5.3-codex", "gpt-5.1", "gpt-5.1-2025-11-13"), date(2027, 4, 1), "gpt-6-sol"),
    *_openai(("gpt-5.4-nano", "gpt-5.4-nano-2026-03-17"), date(2027, 4, 1), "gpt-6-luna"),
    *_openai(
        ("whisper-1", "gpt-4o-transcribe", "gpt-4o-transcribe-diarize"),
        date(2027, 2, 26),
        _TRANSCRIBE,
    ),
    *_openai(
        ("gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe-2025-12-15"),
        date(2027, 2, 26),
        _TRANSCRIBE,
    ),
    *_openai(
        ("gpt-4o-mini-transcribe-2025-03-20",),
        date(2027, 1, 20),
        "gpt-4o-mini-transcribe-2025-12-15",
    ),
    *_openai(
        ("gpt-realtime", "gpt-realtime-2025-08-28", "gpt-4o-realtime"),
        date(2027, 1, 20),
        "gpt-realtime-2.1",
    ),
    *_openai(
        ("gpt-realtime-mini", "gpt-realtime-mini-2025-12-15", "gpt-4o-mini-realtime"),
        date(2027, 1, 20),
        "gpt-realtime-2.1-mini",
    ),
    *_openai(
        (
            "gpt-audio",
            "gpt-audio-2025-08-28",
            "gpt-4o-audio",
            "gpt-audio-mini",
            "gpt-audio-mini-2025-12-15",
            "gpt-4o-mini-audio",
        ),
        date(2027, 1, 20),
        "gpt-audio-1.5",
    ),
    *_openai(
        (
            "gpt-4o-2024-08-06",
            "gpt-4o-2024-11-20",
            "gpt-4o-mini-2024-07-18",
            "gpt-4.1-2025-04-14",
            "gpt-4.1-mini-2025-04-14",
        ),
        date(2027, 1, 14),
        "gpt-6-luna",
    ),
    *_openai(
        (
            "tts-1",
            "tts-1-hd",
            "gpt-4o-mini-tts",
            "gpt-4o-mini-tts-2025-03-20",
            "gpt-4o-mini-tts-2025-12-15",
        ),
        date(2027, 1, 6),
        "gpt-realtime-2.1-mini",
    ),
    *_openai(("gpt-5", "gpt-5-2025-08-07"), date(2026, 12, 11), "gpt-5.6-sol"),
    *_openai(("gpt-5-mini", "gpt-5-mini-2025-08-07"), date(2026, 12, 11), "gpt-5.6-terra"),
    *_openai(("gpt-5-nano", "gpt-5-nano-2025-08-07"), date(2026, 12, 11), "gpt-5.6-luna"),
    *_openai(("o3", "o3-2025-04-16"), date(2026, 12, 11), "gpt-5.6-sol"),
    *_openai(
        ("gpt-5-pro", "gpt-5-pro-2025-10-06", "o3-pro", "o3-pro-2025-06-10"),
        date(2026, 12, 11),
        _SOL_PRO,
    ),
    *_openai(
        ("gpt-image-1-mini", "gpt-image-1.5", "gpt-image-1.5-2025-12-16", "chatgpt-image-latest"),
        date(2026, 12, 1),
        _IMAGE_2_5,
    ),
    *_openai(("gpt-3.5-turbo", "gpt-3.5-turbo-0125"), date(2026, 10, 23), "gpt-5.6-terra"),
    *_openai(
        (
            "gpt-4",
            "gpt-4-0613",
            "gpt-4-1106-preview",
            "gpt-4-turbo",
            "gpt-4-turbo-2024-04-09",
            "gpt-4o-2024-05-13",
            "o1",
            "o1-2024-12-17",
            "o3-mini",
            "o3-mini-2025-01-31",
        ),
        date(2026, 10, 23),
        "gpt-5.6-sol",
    ),
    *_openai(("o1-pro", "o1-pro-2025-03-19"), date(2026, 10, 23), _SOL_PRO),
    *_openai(("gpt-4.1-nano", "gpt-4.1-nano-2025-04-14"), date(2026, 10, 23), "gpt-5.6-luna"),
    *_openai(("o4-mini", "o4-mini-2025-04-16"), date(2026, 10, 23), "gpt-5.6-terra"),
    *_openai(("gpt-image-1",), date(2026, 10, 23), _IMAGE_2_5),
)


def retirement_for(provider: str | None, model: str | None) -> Retirement | None:
    """The announced retirement of `model` on `provider`'s own API, or None."""
    canonical = canonical_provider(provider)
    if canonical is None or not model:
        return None
    matches = [
        row
        for row in RETIREMENTS
        if row.provider == canonical
        and (
            row.model_pattern == model
            if model.endswith("-latest")
            else matches_model(row.model_pattern, model)
        )
    ]
    return max(matches, key=lambda row: len(row.model_pattern), default=None)
