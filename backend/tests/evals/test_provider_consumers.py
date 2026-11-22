"""Provider-native request and replay contracts."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.config import Settings
from app.db.models import ProviderKind
from app.db.rls import bind_project
from app.evals.config import JudgeConfig
from app.evals.evaluators.judge import _body
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.playground.replay_errors import ReplayRefusedError
from app.playground.replay_request import build_replay
from app.playground.replay_source import ReplaySource, SpanFacts, classify
from app.playground.request import build_body, surface_for
from app.playground.schemas import Params
from app.prompts.templates import Message
from tests.evals.judges import JudgeWorld, create_judge, queue_run, run_once
from tests.evals.runs import Sessions
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider
from tests.helpers import new_span_id, new_trace_id, span

GEMINI = "gemini-2.5-flash-lite"
CONTENTS = [{"role": "user", "parts": [{"text": "Hello"}]}]


@pytest.mark.parametrize(
    ("provider", "model", "surface", "key"),
    [
        (ProviderKind.GEMINI, GEMINI, "generate_content", "contents"),
        (ProviderKind.XAI, "grok-4.7", "responses", "input"),
    ],
)
def test_playground_sends_provider_native_shape(
    provider: ProviderKind, model: str, surface: str, key: str
) -> None:
    request = build_body(provider, model, Params(max_tokens=123), [Message("user", "Hello")])
    assert surface_for(provider) == surface
    assert key in request and "messages" not in request


def test_gemini_replay_uses_span_model_and_clamps_native_caps() -> None:
    source = classify(
        SpanFacts(
            "llm",
            "google",
            GEMINI,
            "generate_content",
            {
                "contents": CONTENTS,
                "generationConfig": {"candidateCount": 3, "maxOutputTokens": 5000},
            },
            False,
            {},
        )
    )
    assert isinstance(source, ReplaySource)
    plan = build_replay(
        source,
        credential_provider=ProviderKind.GEMINI,
        model_override=None,
        max_tokens_override=400,
        edited_body=None,
    )
    assert plan.model == GEMINI and plan.surface == "generate_content"
    assert plan.body["generationConfig"] == {"candidateCount": 1, "maxOutputTokens": 400}
    assert plan.fidelity == "modified"


def test_responses_replay_refuses_a_chat_only_credential() -> None:
    source = classify(
        SpanFacts(
            "llm",
            "xai",
            "grok-4.7",
            "responses",
            {"model": "grok-4.7", "input": "Hello"},
            False,
            {},
        )
    )
    assert isinstance(source, ReplaySource)
    with pytest.raises(ReplayRefusedError) as error:
        build_replay(
            source,
            credential_provider=ProviderKind.MISTRAL,
            model_override=None,
            max_tokens_override=None,
            edited_body=None,
        )
    assert error.value.code == "REPLAY_PROVIDER_MISMATCH"


def test_gemini_judge_forces_the_verdict_function() -> None:
    config = JudgeConfig(
        kind="llm_judge", prompt_version_id=uuid.uuid4(), credential_id=uuid.uuid4(), model=GEMINI
    )
    body = _body(ProviderKind.GEMINI, config, [Message("user", "Hello")])
    assert body["contents"] == CONTENTS
    assert body["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["record_verdict"]}
    }


def test_doctor_provider_defaults_to_anthropic_and_can_be_configured() -> None:
    assert Settings(_env_file=None).explain_provider == "anthropic"
    assert Settings(_env_file=None, explain_provider="openai").explain_provider == "openai"


async def _credential(workspace: GatewayWorkspace, provider: str) -> str:
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/credentials",
        json={"provider": provider, "name": provider, "api_key": "provider-test-key"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _gemini_answer(parts: list[dict[str, Any]], reason: str = "STOP") -> dict[str, Any]:
    return {
        "modelVersion": GEMINI,
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": reason}],
        "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 10},
    }


async def _seed_source(
    sessions: Sessions, workspace: GatewayWorkspace, provider: str, model: str, body: dict[str, Any]
) -> dict[str, str]:
    ids = {"trace_id": new_trace_id(), "span_id": new_span_id()}
    async with sessions() as db:
        await bind_project(db, workspace.project_id)
        result = await ingest_spans(
            db,
            IngestTarget(
                project_id=workspace.project_id, org_id=workspace.org_id, capture_payloads=True
            ),
            [span(**ids, provider=provider, model=model, input=body)],
        )
        assert result.accepted == 1, result.rejected
        await db.commit()
    return ids


@pytest.mark.parametrize(("candidates", "override", "exact"), [(1, None, True), (3, 200, False)])
async def test_public_replay_preserves_gemini_surface_and_uses_the_span_model(
    gateway_workspace: GatewayWorkspace,
    session_factory: Sessions,
    fake_provider: FakeProvider,
    candidates: int,
    override: int | None,
    exact: bool,
) -> None:
    world = gateway_workspace
    credential = await _credential(world, "gemini")
    body = {
        "contents": CONTENTS,
        "generationConfig": {"candidateCount": candidates, "maxOutputTokens": 600},
    }
    ids = await _seed_source(session_factory, world, "google", GEMINI, body)
    prefix = f"/api/v1/projects/{world.project_id}"
    source = await world.owner.get(
        f"{prefix}/traces/{ids['trace_id']}/spans/{ids['span_id']}/replay-source",
    )
    assert source.status_code == 200, source.text
    assert source.json()["surface"] == "generate_content"
    assert source.json()["body"] == {**body, "model": GEMINI}
    fake_provider.reply_json(_gemini_answer([{"text": "Replay answer"}]))

    response = await world.owner.post(
        f"{prefix}/playground/replays",
        json={**ids, "credential_id": credential, "max_tokens": override},
    )

    assert response.status_code == 200, response.text
    replay = response.json()
    assert replay["fidelity"] == ("exact" if exact else "modified")
    assert replay["same_request_hash"] is exact
    assert replay["output"] == "Replay answer"
    assert Decimal(replay["cost_usd"]) == Decimal("0.000007")
    [sent] = fake_provider.requests
    assert sent.url.endswith(f"/models/{GEMINI}:generateContent")
    assert sent.json == {
        "contents": CONTENTS,
        "generationConfig": {"candidateCount": 1, "maxOutputTokens": override or 600},
    }


async def test_public_replay_rejects_incompatible_surface_and_hosted_tools_before_reservation(
    gateway_workspace: GatewayWorkspace, session_factory: Sessions, fake_provider: FakeProvider
) -> None:
    world = gateway_workspace
    mistral = await _credential(world, "mistral")
    gemini = await _credential(world, "gemini")
    responses = await _seed_source(
        session_factory,
        world,
        "xai",
        "grok-4.7",
        {"model": "grok-4.7", "input": "Hello"},
    )
    hosted = await _seed_source(
        session_factory,
        world,
        "google",
        GEMINI,
        {"contents": CONTENTS, "tools": [{"googleSearch": {}}]},
    )
    url = f"/api/v1/projects/{world.project_id}/playground/replays"
    for ids, credential, code in (
        (responses, mistral, "REPLAY_PROVIDER_MISMATCH"),
        (hosted, gemini, "REPLAY_UNPRICED_OPTION"),
    ):
        refused = await world.owner.post(url, json={**ids, "credential_id": credential})
        assert (refused.status_code, refused.json()["code"]) == (422, code)
    ledger = await world.owner.get(url)
    assert ledger.status_code == 200 and ledger.json()["items"] == []
    assert fake_provider.requests == []


@pytest.mark.parametrize(
    ("reason", "name", "args", "status", "error"),
    [
        ("STOP", "record_verdict", {"score": 0.9, "reasoning": "On topic."}, "done", None),
        (
            "MAX_TOKENS",
            "record_verdict",
            {"score": 0.9, "reasoning": "On topic."},
            "failed",
            "judge_truncated",
        ),
        (
            "STOP",
            "unrelated_tool",
            {"score": 0.9, "reasoning": "On topic."},
            "failed",
            "verdict_unparseable",
        ),
        (
            "STOP",
            "record_verdict",
            {"score": "0.9", "reasoning": "On topic."},
            "failed",
            "verdict_unparseable",
        ),
    ],
)
async def test_public_gemini_judge_runs_and_settles_verdict(
    judge_world: JudgeWorld,
    reason: str,
    name: str,
    args: dict[str, Any],
    status: str,
    error: str | None,
) -> None:
    credential = await _credential(judge_world.workspace, "gemini")
    judge = await create_judge(
        judge_world,
        budget="1.00",
        credential_id=uuid.UUID(credential),
        model=GEMINI,
    )
    judge_world.fake.reply_json(
        _gemini_answer([{"functionCall": {"name": name, "args": args}}], reason)
    )
    result = await run_once(judge_world, judge, await queue_run(judge_world, judge))
    assert (result.status, result.error, result.cost_usd) == (status, error, Decimal("0.000007"))
    if status == "done":
        [score] = result.scores
        assert score.value == Decimal("0.9") and score.comment == "On topic."
    else:
        assert not result.scores
    [sent] = judge_world.fake.requests
    assert sent.url.endswith(f"/models/{GEMINI}:generateContent")
    assert sent.json["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"] == [
        "record_verdict"
    ]


async def test_public_xai_playground_returns_responses_text(
    gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    world = gateway_workspace
    credential = await _credential(world, "xai")
    fake_provider.reply_json(
        {
            "model": "grok-4.7",
            "status": "completed",
            "output": [
                {"type": "reasoning", "content": [{"type": "output_text", "text": "hidden"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "Visible answer"}]},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 7},
        }
    )
    response = await world.owner.post(
        f"/api/v1/projects/{world.project_id}/playground/runs",
        json={
            "configs": [
                {
                    "credential_id": credential,
                    "model": "grok-4.7",
                    "params": {"max_tokens": 50},
                    "source": {"messages": [{"role": "user", "content": "Hi"}]},
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    [result] = response.json()["results"]
    assert result["output"] == "Visible answer" and result["error"] is None
    [sent] = fake_provider.requests
    assert sent.url == "https://api.x.ai/v1/responses"
    assert sent.json["input"] == [{"role": "user", "content": "Hi"}]
    assert sent.json["max_output_tokens"] == 50 and sent.json["store"] is False
