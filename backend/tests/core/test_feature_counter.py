"""Bet 1's cross-cutting contracts: the feature counter, its sibling metrics, the setting and the
permission (program design §3.3 to §3.5). Each bet adds routes that increment the counter; this
file pins the shape those routes rely on.
"""

from app.config import Settings
from app.core.observability import (
    BILLING_FETCH_DURATION,
    BILLING_FETCH_RUNS,
    BILLING_LINES_UPSERTED,
    COST_EXPLANATION_MISMATCH,
    FEATURE_USED,
    feature_used,
)
from app.core.permissions import Permission, has, permission_class
from app.db.models import MembershipRole
from tests.gateway_support import metric_delta


def test_feature_used_increments_single_label() -> None:
    before = metric_delta(FEATURE_USED, feature="cost_explanation")
    other = metric_delta(FEATURE_USED, feature="reconciliation_report")

    feature_used("cost_explanation")

    assert before.value == 1
    assert other.value == 0
    # `feature` is the only label, ever: a second one would split the confirm/kill signal.
    [metric] = FEATURE_USED.describe()
    assert metric.name == "spanlight_feature_used"
    assert FEATURE_USED._labelnames == ("feature",)


def test_bet_1_metrics_carry_the_documented_names_and_labels() -> None:
    expected = {
        BILLING_FETCH_RUNS: ("spanlight_billing_fetch_runs", ("provider", "outcome")),
        BILLING_FETCH_DURATION: ("spanlight_billing_fetch_duration_seconds", ("provider",)),
        BILLING_LINES_UPSERTED: ("spanlight_billing_lines_upserted", ("provider",)),
        COST_EXPLANATION_MISMATCH: ("spanlight_cost_explanation_mismatch", ()),
    }
    for collector, (name, labels) in expected.items():
        [metric] = collector.describe()
        assert metric.name == name
        assert collector._labelnames == labels


def test_billing_sources_enabled_blank_is_true() -> None:
    # `BILLING_SOURCES_ENABLED=` from a Compose file means "the default", like every other flag.
    assert Settings(_env_file=None, billing_sources_enabled="").billing_sources_enabled is True
    assert Settings(_env_file=None).billing_sources_enabled is True
    off = Settings(_env_file=None, billing_sources_enabled="false")
    assert off.billing_sources_enabled is False


def test_billing_sources_manage_is_a_write_permission_of_admins_and_owners() -> None:
    permission = Permission.BILLING_SOURCES_MANAGE

    assert permission == "billing_sources:manage"
    assert permission_class(permission) == "write"
    assert has(MembershipRole.ADMIN, permission)
    assert has(MembershipRole.OWNER, permission)
    assert not has(MembershipRole.MEMBER, permission)
    assert not has(MembershipRole.VIEWER, permission)


def test_insights_feedback_is_a_write_permission_of_members_and_up() -> None:
    permission = Permission.INSIGHTS_FEEDBACK

    assert permission == "insights:feedback"
    assert permission_class(permission) == "write"
    assert has(MembershipRole.MEMBER, permission)
    assert has(MembershipRole.ADMIN, permission)
    assert has(MembershipRole.OWNER, permission)
    assert not has(MembershipRole.VIEWER, permission)
