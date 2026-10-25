"""Doctor sensitivity routes: the list, the preview and the setting (UX Phase 6, rulings 6-9).

Failure modes: a fixed check shows gates or accepts a setting; the preview counts a finding at a
level its strongest reading never met, or skips an old row with no `peak_strength`; a member or
viewer changes the setting; a repeated PUT writes a second audit event; another organization reads
or changes this project's setting.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project, bypass_rls
from app.insights.sensitivity import ALL_DETECTORS, TUNABLE
from tests.conftest import BrowserFactory
from tests.helpers import Workspace, create_workspace, join_with_role

Sessions = async_sessionmaker[AsyncSession]

_INSERT = text(
    "INSERT INTO insights (id, project_id, kind, severity, status, fingerprint, title, summary, "
    "failure_layer, certainty, evidence, suggested_fix, verification, first_seen_at, "
    "last_seen_at, occurrences, peak_strength) "
    "VALUES (:id, :project_id, :kind, 'warning', 'open', :fingerprint, 'title', 'summary', "
    "'provider', 'measured', jsonb_build_object('trace_ids', '[]'::jsonb, "
    "'metrics', CAST(:metrics AS jsonb), 'window', jsonb_build_object('start', "
    "CAST(:seen AS timestamptz), 'end', CAST(:seen AS timestamptz))), 'fix', 'verify', "
    ":seen, :seen, 1, CAST(:peak AS numeric))"
)


async def seed(
    sessions: Sessions,
    project_id: str,
    *,
    peak: str | None,
    days_ago: float,
    kind: str = "error_spike",
    metrics: str = "{}",
) -> None:
    insight_id = uuid.uuid4()
    async with sessions() as db, db.begin():
        await bind_project(db, uuid.UUID(project_id))
        await db.execute(
            _INSERT,
            {
                "id": insight_id,
                "project_id": project_id,
                "kind": kind,
                "fingerprint": insight_id.hex,
                "metrics": metrics,
                "seen": datetime.now(UTC) - timedelta(days=days_ago),
                "peak": peak,
            },
        )


async def audit_rows(sessions: Sessions, org_id: str) -> list[Any]:
    async with sessions() as db, db.begin():
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT target_id, metadata FROM audit_events "
                "WHERE org_id = :org_id AND action = 'insight.sensitivity_change' ORDER BY id"
            ),
            {"org_id": org_id},
        )
        return list(rows)


def _url(workspace: Workspace, suffix: str = "") -> str:
    return f"/api/v1/projects/{workspace.project_id}/detector-sensitivity{suffix}"


async def test_list_has_every_check_with_fixed_ones_ungated(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    await seed(session_factory, workspace.project_id, peak="0.30", days_ago=1)
    await seed(session_factory, workspace.project_id, peak="0.05", days_ago=2)
    await seed(session_factory, workspace.project_id, peak="0.30", days_ago=40)
    await seed(session_factory, workspace.project_id, peak=None, days_ago=1, kind="unpriced_spend")

    response = await workspace.owner.get(_url(workspace))

    assert response.status_code == 200, response.text
    items = {item["detector"]: item for item in response.json()["items"]}
    assert list(items) == list(ALL_DETECTORS)
    assert len(items) == 15
    assert items["error_spike"] == {
        "detector": "error_spike",
        "tunable": True,
        "level": "high_recall",
        "metric_label": "error rate",
        "gates": {"balanced": "0.10", "high_precision": "0.25"},
        "findings_30d": 2,
        "updated_at": None,
    }
    assert items["unpriced_spend"] == {
        "detector": "unpriced_spend",
        "tunable": False,
        "level": "high_recall",
        "metric_label": None,
        "gates": None,
        "findings_30d": 1,
        "updated_at": None,
    }
    assert {kind for kind, item in items.items() if item["tunable"]} == TUNABLE
    assert items["context_growth"]["gates"] == {"balanced": "50000", "high_precision": "100000"}


async def test_preview_counts_by_strongest_reading(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    for peak in ("0.30", "0.12", "0.05"):
        await seed(session_factory, workspace.project_id, peak=peak, days_ago=3)
    await seed(session_factory, workspace.project_id, peak="0.26", days_ago=20)
    # Out of the window, another kind, and another project's finding do not count.
    await seed(session_factory, workspace.project_id, peak="0.90", days_ago=31)
    await seed(session_factory, workspace.project_id, peak="0.90", days_ago=1, kind="cost_spike")
    other = await create_workspace(await browser_factory("other@example.com"))
    await seed(session_factory, other.project_id, peak="0.90", days_ago=1)

    response = await workspace.owner.get(_url(workspace, "/error_spike/preview"))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "detector": "error_spike",
        "windows": [
            {"days": 7, "high_recall": 3, "balanced": 2, "high_precision": 1},
            {"days": 30, "high_recall": 4, "balanced": 3, "high_precision": 2},
        ],
    }


async def test_preview_reads_evidence_when_peak_is_missing(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    # A row from before peak_strength: its stored evidence decides.
    await seed(
        session_factory,
        workspace.project_id,
        peak=None,
        days_ago=1,
        metrics='{"error_rate": "0.30"}',
    )
    # The stronger of the two readings counts.
    await seed(
        session_factory,
        workspace.project_id,
        peak="0.05",
        days_ago=1,
        metrics='{"error_rate": "0.12"}',
    )
    # No reading at all: never hidden on missing data.
    await seed(session_factory, workspace.project_id, peak=None, days_ago=1)

    response = await workspace.owner.get(_url(workspace, "/error_spike/preview"))

    assert response.status_code == 200, response.text
    assert response.json()["windows"][0] == {
        "days": 7,
        "high_recall": 3,
        "balanced": 3,
        "high_precision": 2,
    }


async def test_admin_sets_level_once_with_one_audit_event(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    admin = await browser_factory("admin@example.com")
    await join_with_role(workspace.owner, admin, workspace.org_id, "admin")
    await seed(session_factory, workspace.project_id, peak="0.30", days_ago=1)
    await seed(session_factory, workspace.project_id, peak="0.05", days_ago=1)

    first = await admin.put(_url(workspace, "/error_spike"), json={"level": "balanced"})
    again = await admin.put(_url(workspace, "/error_spike"), json={"level": "balanced"})

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["detector"] == "error_spike"
    assert body["level"] == "balanced"
    assert body["findings_30d"] == 1
    assert body["updated_at"] is not None
    assert again.status_code == 200, again.text
    assert again.json() == body
    listed = await workspace.owner.get(_url(workspace))
    error_spike = next(i for i in listed.json()["items"] if i["detector"] == "error_spike")
    assert error_spike == body
    events = await audit_rows(session_factory, workspace.org_id)
    assert [(row.target_id, row.metadata) for row in events] == [
        ("error_spike", {"from": "high_recall", "to": "balanced"})
    ]

    back = await admin.put(_url(workspace, "/error_spike"), json={"level": "high_recall"})
    assert back.status_code == 200, back.text
    assert back.json()["findings_30d"] == 2
    events = await audit_rows(session_factory, workspace.org_id)
    assert events[-1].metadata == {"from": "balanced", "to": "high_recall"}


async def test_put_body_is_only_a_known_level(browser_factory: BrowserFactory) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    url = _url(workspace, "/error_spike")

    unknown = await workspace.owner.put(url, json={"level": "paranoid"})
    extra = await workspace.owner.put(
        url, json={"level": "balanced", "project_id": str(uuid.uuid4())}
    )

    assert unknown.status_code == 422
    assert extra.status_code == 422


async def test_members_and_viewers_cannot_change_it(
    browser_factory: BrowserFactory,
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    for role in ("member", "viewer"):
        browser = await browser_factory(f"{role}@example.com")
        await join_with_role(workspace.owner, browser, workspace.org_id, role)
        refused = await browser.put(_url(workspace, "/error_spike"), json={"level": "balanced"})
        assert refused.status_code == 403, (role, refused.text)
        read = await browser.get(_url(workspace))
        assert read.status_code == 200, (role, read.text)


async def test_fixed_or_unknown_check_is_not_tunable(browser_factory: BrowserFactory) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    for detector in ("provider_incident", "unpriced_spend", "no_such_check"):
        preview = await workspace.owner.get(_url(workspace, f"/{detector}/preview"))
        put = await workspace.owner.put(_url(workspace, f"/{detector}"), json={"level": "balanced"})
        for response in (preview, put):
            assert response.status_code == 404, (detector, response.text)
            assert response.json()["code"] == "DETECTOR_NOT_TUNABLE"


async def test_another_organization_gets_404(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    outsider = await create_workspace(await browser_factory("outsider@example.com"))

    for response in (
        await outsider.owner.get(_url(workspace)),
        await outsider.owner.get(_url(workspace, "/error_spike/preview")),
        await outsider.owner.put(_url(workspace, "/error_spike"), json={"level": "balanced"}),
    ):
        assert response.status_code == 404, response.text

    listed = await workspace.owner.get(_url(workspace))
    assert {item["level"] for item in listed.json()["items"]} == {"high_recall"}
    assert await audit_rows(session_factory, workspace.org_id) == []
