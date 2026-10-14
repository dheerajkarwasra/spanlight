"""The Python examples in the documentation run against the real SDK.

A fence marked with ``{/* snippet-test */}`` (MDX) or ``<!-- snippet-test -->`` (Markdown) on
the line before it is executed here. The SDK points at an in-memory ingestion API, so no
snippet touches the network or a model provider. A snippet fails the test when it raises or
when it sends no span to ``POST /v1/traces``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from support import FakeIngestApi, accept_all, decode_body

import spanlight

DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs-site" / "src" / "content" / "docs"

# The marker on its own line, then a ```python fence: the snippet is the fence body. The fence may
# be indented (inside <Steps> or <Tabs>); the closing fence must have the same indent, and that
# indent is removed from every code line.
_MARKER = r"(?:\{/\*\s*snippet-test\s*\*/\}|<!--\s*snippet-test\s*-->)"
_MARKER_LINE = re.compile(rf"^[ \t]*{_MARKER}[ \t]*$", re.MULTILINE)
_SNIPPET = re.compile(
    rf"^[ \t]*{_MARKER}[ \t]*\n(?P<indent>[ \t]*)```python[^\n]*\n"
    rf"(?P<code>.*?)^(?P=indent)```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)


def _extract(text: str) -> list[str]:
    """The code of every marked fence in ``text``, with the fence's indent removed."""
    return [_dedent(match["code"], match["indent"]) for match in _SNIPPET.finditer(text)]


def _dedent(code: str, indent: str) -> str:
    """Remove exactly ``indent`` from the start of each line (a blank line may lack it)."""
    return "".join(line.removeprefix(indent) for line in code.splitlines(keepends=True))


def _pages() -> Iterator[Path]:
    for pattern in ("*.md", "*.mdx"):
        yield from DOCS_ROOT.rglob(pattern)


def _snippets() -> list[tuple[str, str]]:
    """Every marked snippet as ``(id, code)``, id being ``<page>::<index>`` (index from 1)."""
    found: list[tuple[str, str]] = []
    for page in sorted(_pages()):
        text = page.read_text(encoding="utf-8")
        for index, code in enumerate(_extract(text), start=1):
            found.append((f"{page.relative_to(DOCS_ROOT).as_posix()}::{index}", code))
    return found


SNIPPETS = _snippets()


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "GET":
        # Prompts: nothing is stored, so `prompts.get(..., fallback=...)` falls back.
        return httpx.Response(404, json={"title": "Not found"})
    if path.endswith("/v1/scores"):
        return httpx.Response(200, json={"accepted": 1, "rejected": []})
    return accept_all(request)


def _traces_posts(ingest: FakeIngestApi) -> list[httpx.Request]:
    return [
        request
        for request in ingest.requests
        if request.method == "POST" and request.url.path == "/v1/traces"
    ]


def _sent_spans(ingest: FakeIngestApi) -> list[dict[str, Any]]:
    return [span for request in _traces_posts(ingest) for span in decode_body(request)["spans"]]


@pytest.fixture
def snippet_sdk(ingest: FakeIngestApi, monkeypatch: pytest.MonkeyPatch) -> FakeIngestApi:
    """Point the global SDK at ``ingest``, also for snippets that call ``spanlight.init``.

    The shared ``_isolate_environment`` fixture shuts the global client down after the test, so
    snippets never see one another's state.
    """
    ingest.handler = _handler
    real_init = spanlight.init

    def init(**options: Any) -> spanlight.Spanlight:
        options["transport"] = ingest.transport()
        options.setdefault("api_key", "spl_live_test")
        options.setdefault("host", "http://test")
        return real_init(**options)

    monkeypatch.setattr(spanlight, "init", init)
    spanlight.init()
    return ingest


def test_docs_have_marked_snippets():
    markers = sum(len(_MARKER_LINE.findall(page.read_text(encoding="utf-8"))) for page in _pages())
    assert SNIPPETS, "no page marks a Python snippet with snippet-test"
    assert markers == len(SNIPPETS), (
        "a snippet-test marker is not directly followed by a ```python fence "
        f"({markers} markers, {len(SNIPPETS)} snippets)"
    )


@pytest.mark.parametrize(("snippet_id", "code"), SNIPPETS, ids=[item[0] for item in SNIPPETS])
def test_documented_snippets_run(snippet_id: str, code: str, snippet_sdk: FakeIngestApi):
    namespace: dict[str, Any] = {"__name__": "__docs_snippet__"}
    exec(compile(code, snippet_id, "exec"), namespace)

    assert spanlight.flush(timeout=5), f"{snippet_id}: flush timed out"
    assert _traces_posts(snippet_sdk), f"{snippet_id}: nothing was posted to /v1/traces"
    assert _sent_spans(snippet_sdk), f"{snippet_id}: the batches held no span"
    exporter = spanlight.get_client().exporter
    assert exporter is not None
    assert exporter.failed_count == 0, f"{snippet_id}: the server rejected spans"


def test_indented_fence_is_extracted_and_dedented():
    text = (
        "<Steps>\n\n"
        "1. Trace it.\n\n"
        "   {/* snippet-test */}\n"
        "   ```python\n"
        "   def handle(question):\n"
        "       return question\n"
        "\n"
        "   handle('hi')\n"
        "   ```\n\n"
        "</Steps>\n\n"
        "<!-- snippet-test -->\n"
        "```python\n"
        "x = 1\n"
        "```\n"
    )
    assert _extract(text) == [
        "def handle(question):\n    return question\n\nhandle('hi')\n",
        "x = 1\n",
    ]
    assert len(_MARKER_LINE.findall(text)) == 2


def test_fence_closed_at_another_indent_is_not_a_snippet():
    text = "   {/* snippet-test */}\n   ```python\n   x = 1\n```\n"
    assert _extract(text) == []
