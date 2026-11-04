"""A firing alert resolves only after its value reads clear in three consecutive minute passes.

Failure modes pinned here: a rule that resolves on the first clear pass, so a value hovering at
the line fires and resolves every minute; a breach inside the window that does not restart it; an
unknown value that resolves the rule or restarts its window; the window's start kept only in
memory, so a new pass (a new run, or a restarted worker) starts it over; the `ok -> firing` side
(cooldown) changed by the window.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.alerts.evaluate import RuleCondition, RuleState, clear_since, evaluate_rule
from app.alerts.jobs import evaluate_all
from app.alerts.rules import rule_state
from app.alerts.types import Comparator, RuleStateName
from app.config import Settings
from app.db.models import AlertState
from app.rollups.compute import floor_hour
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans

MINUTE = timedelta(minutes=1)
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
RULE = RuleCondition(comparator=Comparator.GT, threshold=Decimal(10), cooldown_minutes=15)
HIGH, LOW = Decimal(11), Decimal(5)


def firing(clear: datetime | None = None) -> RuleState:
    return RuleState(RuleStateName.FIRING, T0, HIGH, clear)


def at(minutes: int) -> datetime:
    return T0 + minutes * MINUTE


def test_first_clear_pass_starts_the_window_and_does_not_resolve() -> None:
    assert evaluate_rule(RULE, LOW, firing(), at(1)) is None
    assert clear_since(RULE, LOW, firing(), at(1)) == at(1)


def test_third_consecutive_clear_minute_resolves() -> None:
    assert evaluate_rule(RULE, LOW, firing(at(1)), at(2)) is None
    assert clear_since(RULE, LOW, firing(at(1)), at(2)) == at(1)
    resolved = evaluate_rule(RULE, LOW, firing(at(1)), at(3))
    assert resolved is not None
    assert (resolved.to_state, resolved.at, resolved.value) == (RuleStateName.OK, at(3), LOW)


def test_window_is_counted_in_minutes_so_pass_jitter_does_not_delay_it() -> None:
    # Passes run once per minute but not on the second: 12:01:59 then 12:03:00.2.
    started = at(1) + timedelta(seconds=59)
    assert evaluate_rule(RULE, LOW, firing(started), at(3) + timedelta(milliseconds=200))


def test_breach_inside_the_window_restarts_it() -> None:
    assert evaluate_rule(RULE, HIGH, firing(at(1)), at(2)) is None
    assert clear_since(RULE, HIGH, firing(at(1)), at(2)) is None
    # Breach, clear, breach: never resolved.
    state = firing()
    for minute, value in enumerate([LOW, HIGH, LOW, LOW, HIGH, LOW], start=1):
        assert evaluate_rule(RULE, value, state, at(minute)) is None
        state = firing(clear_since(RULE, value, state, at(minute)))


def test_unknown_value_neither_resolves_nor_restarts_the_window() -> None:
    assert evaluate_rule(RULE, None, firing(at(1)), at(5)) is None
    assert clear_since(RULE, None, firing(at(1)), at(5)) == at(1)
    assert clear_since(RULE, None, firing(), at(5)) is None
    # The next known clear pass resolves: the window counts from the first clear pass.
    assert evaluate_rule(RULE, LOW, firing(at(1)), at(6)) is not None


def test_stored_window_start_before_the_firing_start_is_ignored() -> None:
    """A `clear_since` older than `since` is a leftover from before the row fired again (a
    1035 -> 1036 rolling deploy wrote none while firing): it reads as no window, so it cannot
    resolve the rule early."""
    stale = AlertState(state=RuleStateName.FIRING, since=T0, clear_since=at(-5), last_value=HIGH)
    assert rule_state(stale).clear_since is None
    kept = AlertState(state=RuleStateName.FIRING, since=T0, clear_since=at(1), last_value=HIGH)
    assert rule_state(kept).clear_since == at(1)


def test_ok_rule_has_no_window_and_its_cooldown_is_unchanged() -> None:
    resolved = RuleState(RuleStateName.OK, at(3), LOW)
    assert clear_since(RULE, LOW, resolved, at(4)) is None
    assert clear_since(RULE, HIGH, resolved, at(4)) is None
    assert evaluate_rule(RULE, HIGH, resolved, at(17)) is None  # inside the 15-minute cooldown
    fired = evaluate_rule(RULE, HIGH, resolved, at(18))
    assert fired is not None and fired.to_state is RuleStateName.FIRING


async def test_evaluation_job_keeps_the_window_across_passes(
    session_factory: SessionFactory, settings: Settings
) -> None:
    """Each `evaluate_all` is a pass of its own (as the worker's per-minute job runs it, before or
    after a restart): the window lives only in `alert_states.clear_since`."""
    (project,) = await new_projects(session_factory, 1)
    rule_id = uuid.uuid4()
    await execute(
        session_factory,
        "INSERT INTO alert_rules (id, project_id, name, kind, metric, window_minutes, comparator, "
        "threshold, cooldown_minutes) VALUES (:id, :project_id, 'Any call', 'threshold', "
        "'llm_calls', 5, 'gt', 0, 0)",
        {"id": rule_id, "project_id": project},
    )
    base = floor_hour(datetime.now(UTC)) + timedelta(minutes=5)

    def minute(n: int) -> datetime:
        return base + n * MINUTE

    async def pass_at(n: int) -> dict[str, object]:
        run = await evaluate_all(session_factory, settings, now=minute(n))
        assert (run.errors, run.projects_failed) == (0, 0)
        (state,) = await fetch(
            session_factory, "SELECT * FROM alert_states WHERE rule_id = :id", {"id": rule_id}
        )
        return state

    await seed_llm_spans(session_factory, project, 1, started_at=minute(0) - timedelta(seconds=30))
    assert (await pass_at(0))["state"] == "firing"
    # The call leaves the 5-minute window at minute 5; a new one breaches again at minute 6.
    assert (await pass_at(5))["clear_since"] == minute(5)
    await seed_llm_spans(session_factory, project, 1, started_at=minute(6) - timedelta(seconds=30))
    breached = await pass_at(6)
    assert (breached["state"], breached["clear_since"]) == ("firing", None)
    assert (await pass_at(12))["clear_since"] == minute(12)
    held = await pass_at(13)
    assert (held["state"], held["clear_since"]) == ("firing", minute(12))
    resolved = await pass_at(14)
    assert (resolved["state"], resolved["since"], resolved["clear_since"]) == (
        "ok",
        minute(14),
        None,
    )
    events = await fetch(
        session_factory,
        "SELECT started_at, resolved_at FROM alert_events WHERE rule_id = :id",
        {"id": rule_id},
    )
    # One episode for the whole flap, resolved when the window completed.
    assert events == [{"started_at": minute(0), "resolved_at": minute(14)}]
