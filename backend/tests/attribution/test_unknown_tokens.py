"""Attribution keeps a group's token totals unknown when none of its calls reported usage.

Failure mode: a trace name whose only call failed before the provider answered read "0 tokens"
on the attribution page, where the project rule is that an unknown value is NULL, never 0. A
group with a reporting call keeps that call's figures.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.attribution.aggregate_sql import grouped_cte
from app.attribution.service import _totals
from app.db.rls import bypass_rls
from tests.attribution.support import SpanSeed, TraceSeed, seed_traces
from tests.jobs.bypass_support import SessionFactory, fetch, new_projects


async def test_group_without_reported_usage_has_unknown_tokens(
    session_factory: SessionFactory,
) -> None:
    [project_id] = await new_projects(session_factory, 1)
    start = datetime(2026, 9, 10, 10, tzinfo=UTC)
    failed = SpanSeed("llm", start, None, None, None, status="error")
    answered = SpanSeed("llm", start, Decimal("0.10"), 50, 5)
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project_id,
            {
                "failed": TraceSeed(start, (failed,), name="failed"),
                "answered": TraceSeed(start, (answered,), name="answered"),
            },
        )
        await db.commit()

    cte = grouped_cte("trace_name", by_day=False, with_environment=False)
    window = {"start": start.replace(hour=0), "end": start.replace(hour=23)}
    rows = await fetch(
        session_factory,
        f"WITH {cte} SELECT * FROM grouped ORDER BY group_value",
        {"project_id": project_id, **window},
    )
    by_name = {row["group_value"]: row for row in rows}
    assert (by_name["failed"]["input_tokens"], by_name["failed"]["output_tokens"]) == (None, None)
    assert (by_name["answered"]["input_tokens"], by_name["answered"]["output_tokens"]) == (50, 5)

    class _Row:
        def __init__(self, values: dict[str, object]) -> None:
            self.__dict__.update(values)

    totals = _totals(_Row(by_name["failed"]))  # type: ignore[arg-type]
    assert (totals.input_tokens, totals.output_tokens) == (None, None)
