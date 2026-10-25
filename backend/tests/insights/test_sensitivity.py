"""Doctor sensitivity gates: one stored metric per check, compared with a constant per level."""

from decimal import Decimal

from app.insights.engine import DETECTORS
from app.insights.sensitivity import ALL_DETECTORS, GATES, TUNABLE, gate_for, passes, strength


def test_high_recall_passes_everything() -> None:
    for kind in ALL_DETECTORS:
        assert passes(kind, "high_recall", {"error_rate": Decimal(0), "share": Decimal(0)})


def test_error_spike_gates() -> None:
    def rate(value: str) -> dict[str, Decimal]:
        return {"error_rate": Decimal(value)}

    assert not passes("error_spike", "balanced", rate("0.09"))
    assert passes("error_spike", "balanced", rate("0.10"))
    assert not passes("error_spike", "high_precision", rate("0.24"))
    assert passes("error_spike", "high_precision", rate("0.25"))
    # Stored evidence holds decimals as strings; the gate reads them the same way.
    assert passes("error_spike", "high_precision", {"error_rate": "0.25"})
    assert gate_for("error_spike", "balanced") == Decimal("0.10")
    assert gate_for("error_spike", "high_recall") is None


def test_latency_strength_is_ratio() -> None:
    metrics = {"p95_ms": Decimal("3000.0"), "baseline_p95_ms": Decimal("1000.0")}
    assert strength("latency_regression", metrics) == 3
    assert passes("latency_regression", "high_precision", metrics)
    assert strength("latency_regression", {"p95_ms": 3000, "baseline_p95_ms": 0}) is None


def test_missing_metric_always_passes() -> None:
    for kind in TUNABLE:
        assert strength(kind, {}) is None
        assert passes(kind, "high_precision", {})
    assert passes("cost_spike", "high_precision", {"ratio": None})
    assert passes("error_spike", "high_precision", {"error_rate": "not a number"})


def test_fixed_checks_are_not_tunable() -> None:
    fixed = {
        "provider_incident",
        "unpriced_spend",
        "truncated_stream_accepted",
        "unsupported_parameter_retried",
        "model_retiring",
    }
    assert set(ALL_DETECTORS) - TUNABLE == fixed
    assert set(GATES) == TUNABLE
    for kind in fixed:
        assert gate_for(kind, "high_precision") is None
        assert passes(kind, "high_precision", {"rounds": 1, "calls": 1})


def test_every_detector_kind_is_listed() -> None:
    assert tuple(detector.kind for detector in DETECTORS) == ALL_DETECTORS
