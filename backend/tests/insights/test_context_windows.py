"""The detector context reads each span kind for the longest window a detector of that kind
declares: llm spans for 24 hours, tool spans for 1 hour (`tool_loop` is the only reader).

The equality gate: detectors are pure functions of their sliced context, so a context whose tool
spans cover 24 hours and one whose tool spans cover 1 hour must give every detector the same
findings, on quiet, busy, baselined and no-baseline projects.
"""

import dataclasses
import inspect
import sys
import uuid

from app.insights import context_queries
from app.insights.context import DetectorContext
from app.insights.context_queries import CONTEXT_WINDOW, TOOL_WINDOW, load_context
from app.insights.detectors import tool_loop
from app.insights.detectors.base import Detector
from app.insights.engine import DETECTORS
from app.insights.schemas import Window
from tests.insights.context_world import NOW, Sessions, busy_spans, seed_world, store


async def _contexts(
    sessions: Sessions, project_id: uuid.UUID
) -> tuple[DetectorContext, DetectorContext]:
    """The context as loaded, and the same one with tool spans read for the llm window."""
    async with sessions() as db:
        ctx = await load_context(db, project_id, NOW)
        assert ctx is not None
        tool_spans, _ = await context_queries._tool_spans(
            db, project_id, Window(start=NOW - CONTEXT_WINDOW, end=NOW)
        )
        await db.commit()
    return ctx, dataclasses.replace(ctx, tool_spans=tool_spans)


def _reads_tool_spans(detector: Detector) -> bool:
    """Whether the detector's module touches `ctx.tool_spans` (detectors read the context in
    their own module; a helper module reading tool spans for one would have to be added here)."""
    return "tool_spans" in inspect.getsource(sys.modules[type(detector).__module__])


def test_each_kind_is_read_for_the_longest_window_its_detectors_declare() -> None:
    assert max(detector.window for detector in DETECTORS) == CONTEXT_WINDOW
    tool_readers = [detector for detector in DETECTORS if _reads_tool_spans(detector)]
    assert [detector.kind for detector in tool_readers] == [tool_loop.detector.kind]
    assert max(detector.window for detector in tool_readers) == TOOL_WINDOW
    assert TOOL_WINDOW < CONTEXT_WINDOW


async def test_tool_spans_are_read_for_the_tool_window_only(session_factory: Sessions) -> None:
    project_id = await store(session_factory, busy_spans(), rollups=False)

    ctx, full = await _contexts(session_factory, project_id)

    assert len(ctx.tool_spans) == 5  # the loop 20 minutes ago
    assert all(span.started_at >= NOW - TOOL_WINDOW for span in ctx.tool_spans)
    assert len(full.tool_spans) == 17  # the 3-hour-old loop is in the 24-hour read
    assert not ctx.truncated


async def test_every_detector_finds_the_same_on_the_short_and_the_full_tool_read(
    session_factory: Sessions,
) -> None:
    projects = await seed_world(session_factory)

    fired: dict[str, set[str]] = {}
    for name, project_id in projects.items():
        ctx, full = await _contexts(session_factory, project_id)
        fired[name] = set()
        for detector in DETECTORS:
            window = Window(start=NOW - detector.window, end=NOW)
            findings = detector.run(ctx.sliced(window))
            assert findings == detector.run(full.sliced(window)), (name, detector.kind)
            fired[name] |= {finding.kind for finding in findings}

    assert fired["quiet"] == set()
    assert fired["busy"] >= {
        "unpriced_spend",
        "rate_limit_pressure",
        "error_spike",
        "truncated_outputs",
        "tool_loop",
    }
    assert fired["other"] == {"unpriced_spend"}
    assert "cost_spike" in fired["baselined"]
