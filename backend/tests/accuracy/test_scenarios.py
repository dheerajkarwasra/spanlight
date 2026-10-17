"""Integrity of the scenario list, and the three provider-path scenarios."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal

import pytest

from accuracy.doctor.runner import plan
from accuracy.doctor.scenarios import CONTROL, DETECTORS, SCENARIOS, by_id, total_est_cost

NEW_SCENARIOS = (
    "rate_limit_pressure:spend_cap",
    "provider_incident:with_incident",
    "rate_limit_pressure:gateway_limiter_negative",
)


def test_every_detector_has_one_positive_and_two_negatives() -> None:
    for detector in DETECTORS:
        group = [s for s in SCENARIOS if s.detector == detector]
        labels = Counter(s.label for s in group)
        assert labels["pos"] >= 1, detector
        assert labels["neg"] >= 2, detector


def test_three_controls_all_negative() -> None:
    controls = [s for s in SCENARIOS if s.detector == CONTROL]
    assert len(controls) == 3
    assert all(s.label == "neg" for s in controls)


def test_ids_projects_and_environments_are_unique() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    projects = [name for s in SCENARIOS for name in s.project_names]
    assert len(projects) == len(set(projects))
    assert all(name.startswith("dr-") for name in projects)
    environments = [s.environment for s in SCENARIOS]
    assert len(environments) == len(set(environments))
    assert all(len(env) <= 64 for env in environments)


def test_faults_never_run_on_a_production_environment() -> None:
    for s in SCENARIOS:
        if s.fault is not None:
            assert s.gateway
            assert s.environment.strip().casefold() != "production"
            assert 0 < s.fault.probability <= 1


def test_modes_are_honest() -> None:
    allowed = {"real", "lab", "synthetic", "mixed"}
    for s in SCENARIOS:
        assert s.mode in allowed, s.id
        if s.mode == "lab":
            assert s.gateway, s.id
            assert s.fault is not None, s.id
        if s.fault is not None:
            assert s.mode == "lab", s.id
        if s.mode == "synthetic":
            assert s.est_cost_usd == 0, s.id
        if s.needs_baseline:
            assert s.detector in {"error_spike", "latency_regression", "cost_spike"}, s.id


def test_synthetic_only_where_allowed() -> None:
    allowed = {"rate_limit_pressure", "unpriced_spend", "provider_incident"}
    for s in SCENARIOS:
        if s.mode == "synthetic":
            assert s.detector in allowed, s.id


def test_incident_scenarios_live_in_their_own_org_with_two_projects() -> None:
    for s in SCENARIOS:
        if s.detector == "provider_incident":
            assert s.org.startswith("incident"), s.id
            assert len(s.project_names) == 2, s.id
        else:
            assert s.org == "main", s.id


def test_estimated_cost_is_within_the_doctor_budget() -> None:
    total = total_est_cost()
    assert total <= Decimal("5.00")
    expensive = [s.id for s in SCENARIOS if s.est_cost_usd > Decimal("1.20")]
    assert expensive == [], expensive


@pytest.mark.parametrize("scenario_id", NEW_SCENARIOS)
def test_the_provider_path_scenarios_exist(scenario_id: str) -> None:
    assert by_id(scenario_id).id == scenario_id


def test_spend_cap_is_a_synthetic_positive_expecting_the_spend_cap_kind() -> None:
    s = by_id("rate_limit_pressure:spend_cap")
    assert (s.detector, s.label, s.mode) == ("rate_limit_pressure", "pos", "synthetic")
    assert s.window == timedelta(minutes=15)
    assert s.expect is not None
    assert s.expect.rate_limit_kind == "spend_cap"
    assert not s.expect.incident_cited
    assert s.est_cost_usd == 0


def test_incident_scenario_is_a_positive_expecting_a_status_page_citation() -> None:
    s = by_id("provider_incident:with_incident")
    assert (s.detector, s.label, s.mode) == ("provider_incident", "pos", "synthetic")
    assert s.window == timedelta(minutes=30)
    assert s.expect is not None
    assert s.expect.incident_cited
    assert s.expect.rate_limit_kind is None
    assert s.org.startswith("incident")
    assert len(s.project_names) == 2


def test_gateway_limiter_negative_bursts_past_a_real_key_limit() -> None:
    """Real gateway traffic, no fault profile: the gateway's own per-key limiter answers the
    429s, which must never read as provider pressure."""
    s = by_id("rate_limit_pressure:gateway_limiter_negative")
    assert (s.detector, s.label, s.mode) == ("rate_limit_pressure", "neg", "real")
    assert s.gateway
    assert s.fault is None
    assert s.key_rpm_limit is not None
    assert s.key_rpm_limit >= 50  # admits enough calls for the detector's 50-call minimum
    assert s.expect is None
    assert Decimal(0) < s.est_cost_usd <= Decimal("0.05")


def test_plan_lists_the_new_scenarios_and_includes_them_in_the_estimate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan(list(SCENARIOS))
    out = capsys.readouterr().out
    for scenario_id in NEW_SCENARIOS:
        assert scenario_id in out
    assert f"estimated total cost: ${total_est_cost():.3f}" in out
    new_cost = sum((by_id(i).est_cost_usd for i in NEW_SCENARIOS), Decimal(0))
    old_cost = sum((s.est_cost_usd for s in SCENARIOS if s.id not in NEW_SCENARIOS), Decimal(0))
    assert total_est_cost() == old_cost + new_cost
