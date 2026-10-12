"""The input-token estimate that budget reservations use before a call. Pure: no I/O.

Every 3 ASCII characters count as one token (prose in Latin scripts runs about 4 characters per
token, so this leaves slack) and every other character as one token of its own, since non-Latin
scripts and emoji take one or more tokens per character. It is an estimate, not a strict ceiling:
digit-, hex- or code-heavy ASCII can run closer to 2 characters per token. Reservations settle
on the provider's reported usage, so the estimate only bounds how far a budget can be overshot.
"""

import math

CHARS_PER_TOKEN = 3


def estimate_input_tokens(ascii_chars: int, other_chars: int = 0) -> int:
    """`ceil(ascii_chars / 3) + other_chars`."""
    return math.ceil(max(ascii_chars, 0) / CHARS_PER_TOKEN) + max(other_chars, 0)


def estimate_text_tokens(text: str) -> int:
    """The input tokens `text` is estimated at."""
    ascii_chars = len(text.encode("ascii", "ignore"))
    return estimate_input_tokens(ascii_chars, len(text) - ascii_chars)
