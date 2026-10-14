"""The eval-gate report and the run link it prints."""

from __future__ import annotations

import json
from typing import Any

import httpx

from spanlight._experiments import run_experiment
from spanlight._experiments_report import render_markdown

CREDENTIAL = "11111111-2222-3333-4444-555555555555"


def _report(totals: dict[str, Any] | None, url: str = "https://x.test/run") -> str:
    return render_markdown(
        title="refunds / answer v1",
        status="done",
        error=None,
        url=url,
        totals=totals,
        fail_below={},
        failures=[],
    )


def test_latency_is_rounded_to_whole_milliseconds():
    report = _report(
        {
            "items": 3,
            "errors": 0,
            "cost_usd": "0.0123",
            "latency_p50_ms": 1234.5678901234567,
            "latency_p95_ms": 2999.9999999999995,
            "scores": {},
        }
    )
    assert "p50 1235 ms, p95 3000 ms." in report
    assert "cost 0.0123 USD" in report


def test_unknown_latency_stays_the_unknown_marker():
    report = _report(
        {"items": 1, "errors": 1, "cost_usd": None, "latency_p50_ms": None, "scores": {}}
    )
    assert "p50 — ms, p95 — ms." in report
    assert "cost — USD" in report


def test_whole_number_latency_has_no_decimals():
    report = _report({"items": 1, "latency_p50_ms": 80, "latency_p95_ms": 80.0, "scores": {}})
    assert "p50 80 ms, p95 80 ms." in report


def test_report_links_the_run_page():
    url = "https://x.test/org/proj/experiments/exp/runs/run"
    assert f"[Open the run]({url})" in _report(None, url)


def _handler(calls: list[tuple[str, str]]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        calls.append((request.method, path))
        project = "/projects/proj-1"
        if request.method == "POST" and path == f"{project}/experiments":
            return httpx.Response(
                200, json={"id": "exp-1", "name": json.loads(request.content)["name"]}
            )
        if request.method == "POST" and path == f"{project}/experiments/exp-1/runs":
            return httpx.Response(200, json={"id": "run-1", "status": "queued"})
        routes: dict[str, Any] = {
            project: {"org_id": "org-1"},
            f"{project}/datasets": {"items": [{"id": "ds-1", "name": "refunds"}]},
            f"{project}/prompts/answer": {"latest": {"version": 1}, "labels": []},
            f"{project}/prompts/answer/versions/1": {"id": "pv-1"},
            f"{project}/experiments": {"items": []},
            f"{project}/experiments/exp-1/runs": {"items": []},
            f"{project}/experiments/exp-1/runs/run-1": {"status": "done", "totals": {"scores": {}}},
        }
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"code": "NOT_FOUND"})

    return handle


def test_run_experiment_returns_the_run_url():
    calls: list[tuple[str, str]] = []
    outcome = run_experiment(
        host="https://spanlight.example.com/",
        token="tok",
        project_id="proj-1",
        dataset="refunds",
        prompt="answer:1",
        evaluators=[],
        fail_below={},
        credential_id=CREDENTIAL,
        model="claude-haiku-4-5",
        poll_s=1,
        transport=httpx.MockTransport(_handler(calls)),
        environ={},
    )
    assert outcome.run_id == "run-1"
    assert outcome.url == "https://spanlight.example.com/org-1/proj-1/experiments/exp-1/runs/run-1"
    assert outcome.passed
