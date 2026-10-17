"""Doctor feedback: "Was this right?" votes on insights, and precision per detector kind."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.core.observability import FEATURE_USED
from app.insights.catalogue import KIND_INFO
from tests.conftest import BrowserFactory
from tests.gateway_support import metric_delta
from tests.helpers import Browser, Workspace, create_workspace, join_with_role
from tests.insights.feedback_support import Sessions, seed_insight, seed_votes, votes_of


@dataclass
class World:
    workspace: Workspace
    member: Browser
    outsider: Browser
    sessions: Sessions

    @property
    def owner(self) -> Browser:
        return self.workspace.owner

    @property
    def project_id(self) -> str:
        return self.workspace.project_id


@pytest.fixture
async def world(browser_factory: BrowserFactory, session_factory: Sessions) -> World:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    member = await browser_factory("member@example.com")
    await join_with_role(owner, member, workspace.org_id, "member")
    outsider = await browser_factory("outsider@example.com")
    return World(workspace, member, outsider, session_factory)


def feedback_url(project_id: str, insight_id: uuid.UUID) -> str:
    return f"/api/v1/projects/{project_id}/insights/{insight_id}/feedback"


def precision_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/insights/precision"


def kinds_by_name(body: dict[str, object]) -> dict[str, dict[str, object]]:
    kinds = body["kinds"]
    assert isinstance(kinds, list)
    return {row["kind"]: row for row in kinds}


async def test_put_records_the_callers_vote(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    delta = metric_delta(FEATURE_USED, feature="insight_feedback")
    response = await world.member.put(
        feedback_url(world.project_id, insight_id), json={"verdict": "right", "note": "Spot on."}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"verdict", "note", "updated_at"}
    assert (body["verdict"], body["note"]) == ("right", "Spot on.")
    assert datetime.fromisoformat(body["updated_at"]).tzinfo is not None
    assert delta.value == 1


async def test_a_second_put_replaces_the_first(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    url = feedback_url(world.project_id, insight_id)
    first = await world.member.put(url, json={"verdict": "right", "note": "Yes."})
    second = await world.member.put(url, json={"verdict": "wrong"})
    assert second.status_code == 200, second.text
    assert (second.json()["verdict"], second.json()["note"]) == ("wrong", None)
    assert second.json()["updated_at"] >= first.json()["updated_at"]
    assert await votes_of(world.sessions, world.project_id, insight_id) == [("wrong", None)]


async def test_an_empty_note_is_stored_as_null(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    url = feedback_url(world.project_id, insight_id)
    response = await world.member.put(url, json={"verdict": "wrong", "note": ""})
    assert response.json()["note"] is None
    assert await votes_of(world.sessions, world.project_id, insight_id) == [("wrong", None)]


async def test_the_note_is_at_most_500_characters(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    url = feedback_url(world.project_id, insight_id)
    too_long = await world.member.put(url, json={"verdict": "right", "note": "x" * 501})
    assert too_long.status_code == 422, too_long.text
    longest = await world.member.put(url, json={"verdict": "right", "note": "x" * 500})
    assert longest.status_code == 200, longest.text


async def test_an_unknown_verdict_is_422(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    url = feedback_url(world.project_id, insight_id)
    response = await world.member.put(url, json={"verdict": "maybe"})
    assert response.status_code == 422, response.text


async def test_delete_removes_the_vote_then_404s(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    url = feedback_url(world.project_id, insight_id)
    await world.member.put(url, json={"verdict": "right"})
    delta = metric_delta(FEATURE_USED, feature="insight_feedback")
    deleted = await world.member.delete(url)
    assert deleted.status_code == 204, deleted.text
    assert await votes_of(world.sessions, world.project_id, insight_id) == []
    again = await world.member.delete(url)
    assert again.status_code == 404, again.text
    assert delta.value == 1


async def test_delete_leaves_other_users_votes(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    url = feedback_url(world.project_id, insight_id)
    await world.owner.put(url, json={"verdict": "right"})
    await world.member.put(url, json={"verdict": "wrong"})
    assert (await world.member.delete(url)).status_code == 204
    assert await votes_of(world.sessions, world.project_id, insight_id) == [("right", None)]


async def test_put_on_an_unknown_insight_is_404(world: World) -> None:
    response = await world.member.put(
        feedback_url(world.project_id, uuid.uuid4()), json={"verdict": "right"}
    )
    assert response.status_code == 404, response.text


async def test_feedback_on_a_resolved_insight_is_allowed(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id, status="resolved")
    response = await world.member.put(
        feedback_url(world.project_id, insight_id), json={"verdict": "wrong"}
    )
    assert response.status_code == 200, response.text
    precision = await world.member.get(precision_url(world.project_id))
    assert kinds_by_name(precision.json())["error_spike"]["wrong"] == 1


async def test_the_detail_shows_my_vote_and_the_counts(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    url = feedback_url(world.project_id, insight_id)
    members_vote = await world.member.put(
        url, json={"verdict": "wrong", "note": "It was our retry loop."}
    )
    owners_vote = await world.owner.put(url, json={"verdict": "right"})
    await seed_votes(world.sessions, world.project_id, insight_id, right=1)
    detail_url = f"/api/v1/projects/{world.project_id}/insights/{insight_id}"

    # `updated_at` is the vote's own time, the same instant the PUT answered with.
    mine = (await world.member.get(detail_url)).json()["feedback"]
    assert mine == {
        "mine": {
            "verdict": "wrong",
            "note": "It was our retry loop.",
            "updated_at": members_vote.json()["updated_at"],
        },
        "right": 2,
        "wrong": 1,
    }
    owners = (await world.owner.get(detail_url)).json()["feedback"]
    assert owners["mine"] == {
        "verdict": "right",
        "note": None,
        "updated_at": owners_vote.json()["updated_at"],
    }


async def test_the_detail_without_votes(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    detail = await world.member.get(f"/api/v1/projects/{world.project_id}/insights/{insight_id}")
    assert detail.json()["feedback"] == {"mine": None, "right": 0, "wrong": 0}


async def test_precision_matches_hand_computed_wilson_values(world: World) -> None:
    spike = await seed_insight(world.sessions, world.project_id, kind="error_spike")
    storm = await seed_insight(world.sessions, world.project_id, kind="retry_storm")
    await seed_votes(world.sessions, world.project_id, spike, right=18, wrong=2)
    await seed_votes(world.sessions, world.project_id, storm, right=3, wrong=2)
    delta = metric_delta(FEATURE_USED, feature="insight_precision")

    response = await world.member.get(precision_url(world.project_id))
    assert response.status_code == 200, response.text
    kinds = kinds_by_name(response.json())
    # 18/20: p = 0.9; z = 1.96, z² = 3.8416, denominator 1.19208, centre 0.83555, half 0.13659.
    assert kinds["error_spike"] == {
        "kind": "error_spike",
        "label": KIND_INFO["error_spike"].label,
        "right": 18,
        "wrong": 2,
        "n": 20,
        "precision": "0.9000",
        "wilson_low": "0.6990",
        "wilson_high": "0.9721",
        "meets_target": True,
    }
    # 3/5: p = 0.6; denominator 1.76832, centre 0.55655, half 0.32583: below n = 20 anyway.
    storm_row = kinds["retry_storm"]
    assert (storm_row["n"], storm_row["precision"]) == (5, "0.6000")
    assert (storm_row["wilson_low"], storm_row["wilson_high"]) == ("0.2307", "0.8824")
    assert storm_row["meets_target"] is False
    assert delta.value == 1


async def test_a_kind_without_votes_has_null_ratios(world: World) -> None:
    await seed_insight(world.sessions, world.project_id, kind="retry_storm")
    kinds = kinds_by_name((await world.member.get(precision_url(world.project_id))).json())
    assert kinds["retry_storm"] == {
        "kind": "retry_storm",
        "label": KIND_INFO["retry_storm"].label,
        "right": 0,
        "wrong": 0,
        "n": 0,
        "precision": None,
        "wilson_low": None,
        "wilson_high": None,
        "meets_target": False,
    }


async def test_a_kind_no_longer_in_the_catalogue_is_labelled_by_kind(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id, kind="retired_detector")
    await seed_votes(world.sessions, world.project_id, insight_id, right=1)
    kinds = kinds_by_name((await world.member.get(precision_url(world.project_id))).json())
    assert kinds["retired_detector"]["label"] == "retired_detector"


async def test_precision_filters_on_when_the_vote_was_last_changed(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    early = datetime(2026, 9, 1, 12, tzinfo=UTC)
    late = datetime(2026, 10, 1, 12, tzinfo=UTC)
    await seed_votes(world.sessions, world.project_id, insight_id, right=2, updated_at=early)
    await seed_votes(world.sessions, world.project_id, insight_id, wrong=1, updated_at=late)
    url = precision_url(world.project_id)

    def counts(body: dict[str, object]) -> tuple[object, object]:
        row = kinds_by_name(body)["error_spike"]
        return row["right"], row["wrong"]

    everything = await world.member.get(url)
    assert counts(everything.json()) == (2, 1)
    since = await world.member.get(url, params={"from": "2026-09-15T00:00:00Z"})
    assert counts(since.json()) == (0, 1)
    until = await world.member.get(url, params={"to": "2026-10-01T12:00:00Z"})
    assert counts(until.json()) == (2, 0)
    backwards = await world.member.get(
        url, params={"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"}
    )
    assert backwards.status_code == 422, backwards.text


async def test_precision_counts_only_this_project(world: World) -> None:
    other = await create_workspace(world.outsider, org_name="Other")
    theirs = await seed_insight(world.sessions, other.project_id, kind="retry_storm")
    await seed_votes(world.sessions, other.project_id, theirs, right=5)
    body = (await world.member.get(precision_url(world.project_id))).json()
    assert body == {"kinds": []}


async def test_another_orgs_member_gets_404_everywhere(world: World) -> None:
    insight_id = await seed_insight(world.sessions, world.project_id)
    await world.member.put(feedback_url(world.project_id, insight_id), json={"verdict": "right"})
    url = feedback_url(world.project_id, insight_id)
    outsider = world.outsider

    assert (await outsider.put(url, json={"verdict": "wrong"})).status_code == 404
    assert (await outsider.delete(url)).status_code == 404
    detail = f"/api/v1/projects/{world.project_id}/insights/{insight_id}"
    assert (await outsider.get(detail)).status_code == 404
    assert (await outsider.get(precision_url(world.project_id))).status_code == 404
    assert await votes_of(world.sessions, world.project_id, insight_id) == [("right", None)]


async def test_another_orgs_insight_through_my_own_project_is_404(world: World) -> None:
    """The insight id alone does not reach across: the outsider's own project path is bound."""
    insight_id = await seed_insight(world.sessions, world.project_id)
    other = await create_workspace(world.outsider, org_name="Other")
    url = feedback_url(other.project_id, insight_id)
    assert (await world.outsider.put(url, json={"verdict": "wrong"})).status_code == 404
    assert (await world.outsider.delete(url)).status_code == 404
    assert await votes_of(world.sessions, world.project_id, insight_id) == []
