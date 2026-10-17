"""The scoring arithmetic on hand-made result rows."""

import math
from decimal import Decimal
from typing import Any

from accuracy.doctor import score as score_module
from accuracy.doctor.score import control_findings, cross_detector_findings, markdown, score
from app.insights.precision import wilson


def row(
    scenario: str,
    detector: str,
    label: str,
    fired: bool,
    kinds: list[str] | None = None,
    matched: bool = True,
) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "detector": detector,
        "label": label,
        "fired": fired,
        "finding_kinds_in_project": kinds if kinds is not None else ([detector] if fired else []),
        "matched_own_traces": matched,
        "run": {"calls": 1, "failures": 0, "error": None},
    }


ROWS = [
    row("a:pos", "a", "pos", True),
    row("a:neg-1", "a", "neg", False),
    row("a:neg-2", "a", "neg", True, kinds=["a", "b"]),
    row("b:pos", "b", "pos", False),
    row("b:neg-1", "b", "neg", False),
    row("b:neg-2", "b", "neg", False, kinds=["a"]),
    row("c:pos", "c", "pos", True, matched=False),
    row("c:neg-1", "c", "neg", False),
    row("c:neg-2", "c", "neg", False),
    row("control:chat", "control", "neg", False, kinds=["tool_loop"]),
    row("control:agent", "control", "neg", False, kinds=[]),
]


def test_confusion_counts() -> None:
    counts = score(ROWS)
    assert (counts["a"].tp, counts["a"].fn, counts["a"].fp, counts["a"].tn) == (1, 0, 1, 1)
    assert (counts["b"].tp, counts["b"].fn, counts["b"].fp, counts["b"].tn) == (0, 1, 0, 2)
    assert (counts["c"].tp, counts["c"].fn, counts["c"].fp, counts["c"].tn) == (1, 0, 0, 2)
    assert "control" not in counts


def test_precision_recall_and_unknowns() -> None:
    counts = score(ROWS)
    assert counts["a"].precision == 0.5
    assert counts["a"].recall == 1.0
    assert counts["b"].precision is None
    assert counts["b"].recall == 0.0
    assert counts["c"].unmatched_evidence == 1
    assert counts["a"].unmatched_evidence == 0


def test_the_harness_scores_with_the_product_wilson_interval() -> None:
    """One Wilson interval for the API, the CLI and the harness."""
    assert score_module.wilson is wilson


def test_wilson_interval() -> None:
    assert wilson(0, 0) is None
    bounds = wilson(1, 1)
    assert bounds is not None
    assert math.isclose(bounds[0], 0.2065, abs_tol=1e-3)
    assert bounds[1] == Decimal("1.0000")
    bounds = wilson(5, 10)
    assert bounds is not None
    assert math.isclose(bounds[0], 0.2366, abs_tol=1e-3)
    assert math.isclose(bounds[1], 0.7634, abs_tol=1e-3)


def test_cross_detector_and_control_findings() -> None:
    assert cross_detector_findings(ROWS) == [
        ("a:neg-2", "a", ["b"]),
        ("b:neg-2", "b", ["a"]),
    ]
    assert control_findings(ROWS) == [("control:chat", ["tool_loop"])]


def test_markdown_mentions_every_detector_and_the_miss() -> None:
    text = markdown(ROWS)
    for detector in ("a", "b", "c", "**all**"):
        assert f"| {detector} |" in text
    assert "b:pos" in text  # the positive that did not fire
    assert "control:chat" in text
    assert "0.50 (1/2, 95 % 0.09-0.91)" in text  # a's precision with its Wilson interval


def test_markdown_lists_scenario_expectations_and_whether_they_were_met() -> None:
    rows = [
        {
            **row("x:spend_cap", "x", "pos", True),
            "expectation": {"rate_limit_kind": "spend_cap"},
            "rate_limit_kinds": ["spend_cap"],
            "expectation_met": True,
        },
        {
            **row("y:with_incident", "y", "pos", True),
            "expectation": {"incident_cited": True},
            "incident_citations": [{"id": None, "title": None}],
            "expectation_met": False,
        },
        row("z:pos", "z", "pos", True),
    ]
    text = markdown(rows)
    assert "### Scenario expectations" in text
    assert "| x:spend_cap | rate_limit_kind=spend_cap | met |" in text
    assert "| y:with_incident | incident_cited | not met |" in text
    assert "z:pos" not in text.split("### Scenario expectations")[1]
