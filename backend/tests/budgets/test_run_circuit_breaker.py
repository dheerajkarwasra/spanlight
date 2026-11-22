"""Unit tests for per-run hard circuit breakers."""

from decimal import Decimal

from app.budgets.run_guard import check_run_limits
from app.gateway.trace_headers import TraceHeaders, parse_max_steps, parse_run_budget


def test_run_header_parsers_accept_valid_and_drop_invalid() -> None:
    assert parse_run_budget("1.25") == Decimal("1.25000000")
    assert parse_run_budget("0") is None
    assert parse_run_budget("-5") is None
    assert parse_run_budget("NaN") is None
    assert parse_run_budget("100001") is None

    assert parse_max_steps("10") == 10
    assert parse_max_steps("0") is None
    assert parse_max_steps("-1") is None
    assert parse_max_steps("abc") is None
    assert parse_max_steps("20000") is None

    headers = TraceHeaders.from_headers(
        {
            "X-Spanlight-Run-Budget-USD": "2.50",
            "X-Spanlight-Max-Steps": "12",
        }
    )
    assert headers.run_budget_usd == Decimal("2.50000000")
    assert headers.max_steps == 12


def test_check_run_limits_enforces_steps_and_budget() -> None:
    assert (
        check_run_limits(
            2,
            Decimal("0.50"),
            Decimal("0.10"),
            max_steps=5,
            run_budget_usd=Decimal("1.00"),
            estimate_usd=Decimal("0.40"),
        )
        is None
    )
    step_refusal = check_run_limits(
        5,
        Decimal("0.50"),
        Decimal("0"),
        max_steps=5,
        run_budget_usd=Decimal("10.00"),
        estimate_usd=Decimal("0.10"),
    )
    assert step_refusal is not None and "5 steps" in step_refusal

    budget_refusal = check_run_limits(
        1,
        Decimal("0.60"),
        Decimal("0.30"),
        max_steps=10,
        run_budget_usd=Decimal("1.00"),
        estimate_usd=Decimal("0.20"),
    )
    assert budget_refusal is not None and "budget" in budget_refusal.lower()
