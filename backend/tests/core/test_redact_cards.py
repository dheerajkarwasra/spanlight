"""Card numbers are redacted even when other digits sit right next to them.

The card pattern matches the longest run of digit groups it can, so a card followed by a CVV, an
expiry or a quantity used to be read as one 19-digit number that fails the Luhn check and was
kept whole. Every 13-19 digit window inside such a run is checked instead.
"""

import random
import sys
import time

import pytest

from app.core.redact import REDACTED, redact_text
from app.ingest.schemas import MAX_BODY_BYTES


def _traced() -> bool:
    """Whether a tracer such as coverage slows every line, which makes a CPU bound meaningless."""
    coverage = sys.modules.get("coverage")
    return sys.gettrace() is not None or (
        coverage is not None and coverage.Coverage.current() is not None
    )


skip_when_traced = pytest.mark.skipif(_traced(), reason="CPU bound is meaningless under a tracer")


@pytest.mark.parametrize(
    "text",
    [
        "card 4242 4242 4242 4242 123",
        "card 4242424242424242 12/26",
        "card 4242 4242 4242 4242 12/26",
        "qty 2 4242424242424242",
        "card 4242-4242-4242-4242-12",
        "ref 99 4242 4242 4242 4242 7",
        # Past 19 digits in all: the card is the last four groups of the run.
        "ids 1234 5678 4242 4242 4242 4242",
    ],
)
def test_card_next_to_a_cvv_expiry_or_quantity_is_redacted(text: str) -> None:
    redacted = redact_text(text)

    assert "4242" not in redacted
    assert REDACTED in redacted


def test_the_neighbouring_digits_are_kept() -> None:
    assert redact_text("card 4242 4242 4242 4242 123") == f"card {REDACTED} 123"
    assert redact_text("qty 2 4242424242424242") == f"qty 2 {REDACTED}"


@pytest.mark.parametrize(
    "text",
    [
        # One unseparated run is judged whole: its inner windows are not split out, so a long
        # numeric id is not redacted for containing a Luhn-valid slice.
        "order 12345678901234567890 ref 42",
        "id 4242424242424242123",
        # Grouped digits where no run of whole groups passes the Luhn check.
        "ref 1234 5678 9012 3456 7890",
        "ref 4000 1234 5678 9012 34",
    ],
)
def test_digits_with_no_luhn_valid_group_window_are_unchanged(text: str) -> None:
    assert redact_text(text) == text


def test_a_lone_card_number_is_still_redacted() -> None:
    assert redact_text("pay with 4242 4242 4242 4242 now") == f"pay with {REDACTED} now"


def test_a_card_in_non_ascii_digits_next_to_other_digits_is_redacted() -> None:
    # `\d` matches every Unicode decimal digit; the Luhn check must read them all, not raise.
    arabic_indic = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    text = "card 4242 4242 4242 4242 123".translate(arabic_indic)

    assert redact_text(text) == f"card {REDACTED} {'123'.translate(arabic_indic)}"


@pytest.mark.parametrize(
    "text",
    [
        "tokens 15 23 991 4 2 8 1 77 3 5 6 9 12 44 8 1",
        "1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20",
        "0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1",
        "dates 2026-10-13 2026-10-14 2026-10-15",
        "call 415-555-2671 or 212-555-0198 or 646-555-0143",
    ],
)
def test_lists_of_small_numbers_dates_and_phone_numbers_are_unchanged(text: str) -> None:
    # Only card-shaped runs are searched: groups of four or more digits, the last of two or more.
    assert redact_text(text) == text


def test_a_card_behind_small_numbers_is_still_redacted() -> None:
    assert redact_text("1 2 3 4 5 6 7 8 9 4242 4242 4242 4242") == f"1 2 3 4 5 6 7 8 9 {REDACTED}"


@skip_when_traced
@pytest.mark.parametrize("unit", ["1 ", "0 ", "4242 ", "0000 ", "4242-42 "])
def test_redacting_a_body_sized_run_of_digit_groups_stays_cheap(unit: str) -> None:
    # Redaction runs on the event loop before payloads are cut to 32 KB, so its cost on a whole
    # body of crafted digit groups bounds how long one ingest request can hold the loop. Measured
    # as this process's CPU time, not wall time, so other work on a busy machine does not count;
    # an absolute bound rather than a 1 MiB / 4 MiB scaling ratio, because a ratio of two short
    # timings swings more under load than one long one. These shapes cost up to about 0.8 s on a
    # laptop, and nearly 4 s on an efficiency core when pytest-xdist fills every core; the bound
    # leaves room for that and still fails the 6 s that a window search over single-digit groups
    # cost.
    text = unit * (MAX_BODY_BYTES // len(unit))

    started = time.process_time()
    redact_text(text)

    assert time.process_time() - started < 5.0


@skip_when_traced
def test_redacting_a_body_of_random_digit_groups_stays_cheap() -> None:
    # Random four-digit groups miss the per-group cache every time, the costliest shape (about
    # 1 s of CPU on a laptop). Same bound and reasoning as the repeated shapes above.
    groups = random.Random(20261013).choices(range(10_000), k=MAX_BODY_BYTES // 5)  # noqa: S311
    text = " ".join(f"{group:04d}" for group in groups)

    started = time.process_time()
    redact_text(text)

    assert time.process_time() - started < 5.0
