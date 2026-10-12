"""Health checks, Prometheus metrics, request ids and OpenAPI."""

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.health import WORKER_HEARTBEAT_MAX_AGE_SECONDS
from app.config import Settings
from app.jobs.worker_heartbeat import beat


async def test_live_and_ready(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}

    # WORKER_REQUIRED is on by default: with no worker heartbeat the api is not ready.
    no_worker = await client.get("/health/ready")
    assert no_worker.status_code == 503
    assert no_worker.json() == {
        "status": "unavailable",
        "database": "ok",
        "migrations": "ok",
        "worker_heartbeat_age_s": None,
        "outbox_backlog": 0,
        "alert_evaluation_lag_s": None,
        "gateway_mode": "embedded",
    }

    await beat(session_factory, "test-worker", "test")
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    age = body.pop("worker_heartbeat_age_s")
    assert isinstance(age, float) and 0 <= age < WORKER_HEARTBEAT_MAX_AGE_SECONDS
    assert body == {
        "status": "ok",
        "database": "ok",
        "migrations": "ok",
        "outbox_backlog": 0,
        "alert_evaluation_lag_s": None,
        "gateway_mode": "embedded",
    }


async def test_metrics_requires_bearer_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/metrics")).status_code == 401
    wrong = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    ok = await client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"})
    assert ok.status_code == 200
    assert "spanlight_http_requests_total" in ok.text


async def test_request_id_is_echoed_or_generated(client: httpx.AsyncClient) -> None:
    echoed = await client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
    generated = await client.get("/health/live", headers={"X-Request-ID": "bad id with spaces"})
    assert generated.headers["x-request-id"] != "bad id with spaces"


async def test_unknown_route_is_problem_json(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/nope")
    body = response.json()
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["request_id"] == response.headers["x-request-id"]


async def test_openapi_is_served(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()
    assert "/v1/traces" in spec["paths"]
    assert (await client.get("/api/docs")).status_code == 200


async def test_metrics_label_requests_by_route_template(client: httpx.AsyncClient) -> None:
    await client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000/traces")
    metrics = await client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"})
    assert 'route="/api/v1/projects/{project_id}/traces"' in metrics.text


def test_https_deployment_requires_a_real_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(app_base_url="https://spanlight.example.com", _env_file=None)
    configured = Settings(
        app_base_url="https://spanlight.example.com", secret_key="s3cret-value", _env_file=None
    )
    assert configured.secure_cookies is True
