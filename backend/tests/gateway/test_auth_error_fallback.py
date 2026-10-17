"""Pilot defect D10: falling back to another credential on `401`/`403` is opt-in.

By default a rejected key ends the call, so a revoked key is noticed instead of being covered by
another credential that quietly takes on its spend. A route that lists `auth_error` in
`fallback.on` moves on to its next target, and it may only do so with two targets on different
credentials: falling back to the same key would be refused the same way.
"""

import uuid
from typing import Any

from app.gateway.execute import execute
from app.gateway.route_config import FallbackCondition, FallbackPolicy, RouteConfig, load_stored
from tests.gateway.support import chat_frames, chat_request, read_to_end
from tests.gateway_fixtures import OPENAI_API_KEY, GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import FakeProvider, read_gateway_span

SECOND_OPENAI_KEY = "sk-test-openai-second-credential"
FOUR_CONDITIONS = [
    FallbackCondition.STATUS_5XX,
    FallbackCondition.RATE_LIMITED,
    FallbackCondition.TIMEOUT,
    FallbackCondition.CONNECTION_ERROR,
]
OPT_IN = [*(condition.value for condition in FOUR_CONDITIONS), "auth_error"]
OPT_IN_MESSAGE = "fallback.on: auth_error needs two targets on different credentials"


def _auth_error(status: int) -> dict[str, Any]:
    message = "Incorrect API key provided." if status == 401 else "Project has no access."
    return {"error": {"message": message, "type": "invalid_request_error", "code": None}}


async def _second_openai_credential(workspace: GatewayWorkspace) -> uuid.UUID:
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/credentials",
        json={"name": "openai-second", "provider": "openai", "api_key": SECOND_OPENAI_KEY},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _save_default_route(
    workspace: GatewayWorkspace, credential_ids: list[uuid.UUID], on: list[str] | None
) -> None:
    """Replace the default route's config: one attempt per target, `fallback.on` as given."""
    fallback: dict[str, Any] = {} if on is None else {"on": on}
    config = {
        "targets": [{"credential_id": str(credential_id)} for credential_id in credential_ids],
        "retry": {"max_attempts": 1},
        "fallback": fallback,
    }
    response = await workspace.owner.put(
        f"/api/v1/projects/{workspace.project_id}/gateway/routes/{workspace.route_id}",
        json={"config": config, "expected_version": 1},
    )
    assert response.status_code == 200, response.text


async def _create_route(workspace: GatewayWorkspace, credential_ids: list[uuid.UUID]) -> Any:
    config = {
        "targets": [{"credential_id": str(credential_id)} for credential_id in credential_ids],
        "retry": {},
        "fallback": {"on": OPT_IN},
    }
    return await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/gateway/routes",
        json={"name": "auth-fallback", "config": config},
    )


async def test_401_gives_up_by_default(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Any,
) -> None:
    second = await _second_openai_credential(gateway_workspace)
    await _save_default_route(
        gateway_workspace, [gateway_workspace.openai_credential_id, second], on=None
    )
    fake_provider.reply_json(_auth_error(401), status=401)

    result = await execute(chat_request(), await gateway_context())

    assert (result.status, result.stream) == (401, None)
    assert result.headers["X-Spanlight-Attempts"] == "1"
    assert len(fake_provider.requests) == 1
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["error_class"]) == ("error", "auth")
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert span["attributes"]["spanlight.gateway.fallbacks"] == 0


async def _assert_falls_back(
    status: int,
    gateway_context: GatewayContextFactory,
    workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Any,
) -> None:
    second = await _second_openai_credential(workspace)
    await _save_default_route(workspace, [workspace.openai_credential_id, second], on=OPT_IN)
    fake_provider.reply_json(_auth_error(status), status=status)
    fake_provider.reply_stream(chat_frames())

    result = await execute(chat_request(), await gateway_context())
    assert result.stream is not None
    await read_to_end(result.stream)

    assert result.status == 200
    assert (fake_provider.pending, fake_provider.open_streams) == (0, 0)
    # The weighted first pick may land on either target; the fallback used the other key.
    keys = {sent.headers["authorization"] for sent in fake_provider.requests}
    assert keys == {f"Bearer {OPENAI_API_KEY}", f"Bearer {SECOND_OPENAI_KEY}"}
    span = await read_gateway_span(session_factory, workspace.project_id)
    assert (span["status"], span["status_message"]) == ("ok", None)
    assert span["attributes"]["spanlight.gateway.attempts"] == 2
    assert span["attributes"]["spanlight.gateway.fallbacks"] == 1


async def test_401_falls_back_when_opted_in(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Any,
) -> None:
    await _assert_falls_back(
        401, gateway_context, gateway_workspace, fake_provider, session_factory
    )


async def test_403_falls_back_when_opted_in(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Any,
) -> None:
    await _assert_falls_back(
        403, gateway_context, gateway_workspace, fake_provider, session_factory
    )


def _assert_opt_in_refused(response: Any) -> None:
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert problem["errors"] == [{"field": "config", "message": OPT_IN_MESSAGE}]


async def test_opt_in_with_one_credential_is_422(gateway_workspace: GatewayWorkspace) -> None:
    response = await _create_route(gateway_workspace, [gateway_workspace.openai_credential_id])

    _assert_opt_in_refused(response)


async def test_opt_in_with_two_targets_on_one_credential_is_422(
    gateway_workspace: GatewayWorkspace,
) -> None:
    openai = gateway_workspace.openai_credential_id

    response = await _create_route(gateway_workspace, [openai, openai])

    _assert_opt_in_refused(response)


async def test_opt_in_with_two_credentials_is_accepted(
    gateway_workspace: GatewayWorkspace,
) -> None:
    credentials = [
        gateway_workspace.openai_credential_id,
        gateway_workspace.anthropic_credential_id,
    ]

    response = await _create_route(gateway_workspace, credentials)

    assert response.status_code == 201, response.text
    assert response.json()["config"]["fallback"]["on"] == OPT_IN


def test_load_stored_reads_an_old_config_without_the_value() -> None:
    # Saved before `auth_error` existed: the four conditions spelt out, a single target.
    stored = {
        "targets": [{"credential_id": str(uuid.uuid4()), "weight": 1, "model_aliases": {}}],
        "retry": {"max_attempts": 2},
        "fallback": {"on": [condition.value for condition in FOUR_CONDITIONS]},
        "timeout_ms": 60_000,
    }

    config = load_stored(stored)

    assert config.fallback.on == FOUR_CONDITIONS
    assert FallbackCondition.AUTH_ERROR not in config.fallback.on


def test_default_policy_lists_four_conditions() -> None:
    config = RouteConfig.model_validate(
        {"targets": [{"credential_id": str(uuid.uuid4())}], "retry": {}, "fallback": {}}
    )

    assert FallbackPolicy().on == FOUR_CONDITIONS
    assert config.fallback.on == FOUR_CONDITIONS
