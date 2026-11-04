"""Gateway overload must refuse before body/auth and retain slots through cleanup."""

import asyncio
from contextlib import AsyncExitStack
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.gw import MAX_REQUEST_BYTES
from app.config import Settings
from app.main import create_app
from tests.gateway.admission_support import CHAT, unfinished_request, wait_active, world
from tests.gateway_support import drain_recorder
from tests.integration.network_support import running_application


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize(
    ("path", "error_type"),
    [
        ("/gw/v1/chat/completions", "server_error"),
        ("/gw/v1/responses", "server_error"),
        ("/gw/v1/messages", "overloaded_error"),
        ("/gw/v1/models", "server_error"),
        ("/gw/v1/models", "overloaded_error"),
    ],
)
async def test_full_gateway_rejects_unfinished_upload_before_auth_or_provider(
    gateway_settings: Settings,
    standalone: bool,
    path: str,
    error_type: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Bypassing overload or releasing at headers lets this body hang or hit auth.
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, standalone)
        streams = [
            await stack.enter_async_context(
                w.client.stream("POST", "/gw/v1/chat/completions", json=CHAT)
            )
            for _ in range(2)
        ]
        assert [r.status_code for r in streams] == [200, 200]
        await wait_active(w.provider, 2)
        status, headers, body = await unfinished_request(
            w.base,
            path,
            w.key,
            anthropic=path.endswith("/models") and error_type == "overloaded_error",
        )
        assert status == 503
        assert headers["retry-after"] == "1"
        assert headers["x-request-id"] == "admission-overload"
        assert body["error"]["type"] == error_type
        assert body["error"]["spanlight_code"] == "SERVICE_UNAVAILABLE"
        assert headers["x-spanlight-code"] == "SERVICE_UNAVAILABLE"
        if error_type == "overloaded_error":
            assert body["request_id"] == "admission-overload"
        invalid_status, _, _ = await unfinished_request(
            w.base, "/gw/v1/chat/completions", "invalid-key"
        )
        assert invalid_status == 503
        assert w.provider.calls == 2
        assert (await w.client.get("/health/live")).status_code == 200
        assert (await w.client.get("/health/ready")).status_code == 200
        assert (
            await w.client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"})
        ).status_code == 200
        if not standalone:
            shown = await w.client.get(
                f"/api/v1/orgs/{w.workspace.org_id}/projects",
                headers={
                    "Authorization": "",
                    "Cookie": "; ".join(f"{k}={v}" for k, v in w.owner.http.cookies.items()),
                },
            )
            assert shown.status_code == 200
        w.provider.release.set()
        for response in streams:
            await response.aread()
        await wait_active(w.provider, 0)
        await drain_recorder()
        history = await w.owner.get(f"/api/v1/projects/{w.workspace.project_id}/traces")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 2
        assert all(item["cost_usd"] is None for item in history.json()["items"])
        recovered = await w.client.get("/gw/v1/models")
        assert recovered.status_code == 200


async def test_api_saturation_does_not_spend_gateway_capacity(gateway_settings: Settings) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False)
        held = asyncio.create_task(w.client.get("/api/admission-test"))
        try:
            await asyncio.wait_for(w.api_entered.wait(), 2)
            assert (await w.client.get("/api/admission-test")).status_code == 503
            response = await stack.enter_async_context(
                w.client.stream("POST", "/gw/v1/chat/completions", json=CHAT)
            )
            assert response.status_code == 200
            await wait_active(w.provider, 1)
            assert (await w.client.get("/health/live")).status_code == 200
            w.provider.release.set()
            await response.aread()
        finally:
            w.api_release.set()
            await held


@pytest.mark.parametrize("mode", ["disabled", "standalone"])
async def test_unmounted_gateway_keeps_problem_shape(gateway_settings: Settings, mode: str) -> None:
    application = create_app(
        gateway_settings.model_copy(update={"gateway_mode": mode, "gateway_max_in_flight": 1})
    )
    async with (
        running_application(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver"
        ) as client,
    ):
        response = await client.get("/gw/v1/models")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")


def test_gateway_capacity_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.gateway_max_in_flight == 512
    assert settings.gateway_max_in_flight_bytes == 83_886_080
    assert settings.gateway_max_in_flight_per_key == 256


@pytest.mark.parametrize(
    "setting",
    ["gateway_max_in_flight", "gateway_max_in_flight_bytes", "gateway_max_in_flight_per_key"],
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), 1.5])
def test_gateway_capacity_rejects_invalid_settings(setting: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{setting: value})


def test_byte_budget_floor_matches_request_cap() -> None:
    # config.py repeats app.api.gw.MAX_REQUEST_BYTES as a literal to avoid a circular import.
    assert Settings(_env_file=None, gateway_max_in_flight_bytes=MAX_REQUEST_BYTES)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gateway_max_in_flight_bytes=MAX_REQUEST_BYTES - 1)


@pytest.mark.parametrize(
    "setting",
    ["gateway_max_in_flight", "gateway_max_in_flight_bytes", "gateway_max_in_flight_per_key"],
)
def test_gateway_capacity_blank_and_environment(
    monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    # Compose forwards blank values; configured positive values must reach the factory.
    configured = MAX_REQUEST_BYTES + 3
    monkeypatch.setenv(setting.upper(), str(configured))
    assert getattr(Settings(_env_file=None), setting) == configured
    monkeypatch.setenv(setting.upper(), "")
    blank = getattr(Settings(_env_file=None), setting)
    assert blank == Settings.model_fields[setting].default
    assert blank == getattr(Settings(_env_file=None, **{setting: ""}), setting)
