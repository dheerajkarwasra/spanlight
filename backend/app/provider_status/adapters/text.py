"""Feed text to plain, storable text. Pure.

Status pages put HTML in titles and update bodies. Nothing from a feed is ever rendered as HTML,
so it is reduced to text here: entities unescaped first (so an escaped `&lt;script&gt;` is
stripped like a literal one), then tags removed, then whitespace collapsed.
"""

import html
import re

from app.core.text import clean_text

# A tag starts with a letter, `/`, `!` or `?` right after `<`, so "latency < 5 s" survives. An
# unclosed tag at the end of the text is removed too.
_TAG = re.compile(r"</?[A-Za-z!?][^>]*(?:>|$)")
_WHITESPACE = re.compile(r"\s+")


def html_to_text(value: str) -> str:
    """`value` with entities unescaped, tags removed and whitespace collapsed to single spaces."""
    without_tags = _TAG.sub(" ", html.unescape(value))
    return _WHITESPACE.sub(" ", without_tags).strip()


def clamp(value: str, limit: int) -> str:
    """`value` without NUL (and lone surrogates), cut to at most `limit` characters."""
    return clean_text(value)[:limit]
