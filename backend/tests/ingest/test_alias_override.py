"""An organization's price override saved under a provider alias prices spans sent under another.

Failure mode: an override saved as `gemini` is compared with the span's folded provider (`google`)
as written, so it never matches and the span keeps the built-in price, or none.
"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import Workspace, new_trace_id, span
from tests.ingest.support import ingest_native, stored_spans


async def _post_override(workspace: Workspace, provider: str) -> str:
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/price-overrides",
        json={
            "provider": provider,
            "model_pattern": "gemini-2.5-flash",
            "input_per_mtok": "100",
            "output_per_mtok": "200",
            "effective_from": "2025-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.parametrize(
    ("override_provider", "span_provider"),
    [("gemini", "google"), ("google", "vertex_ai")],
)
async def test_override_under_an_alias_prices_the_span(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    override_provider: str,
    span_provider: str,
) -> None:
    override_id = await _post_override(workspace, override_provider)
    batch = [
        span(
            trace_id=new_trace_id(),
            name="chat gemini-2.5-flash",
            provider=span_provider,
            model="gemini-2.5-flash",
        )
    ]

    assert (await ingest_native(client, workspace, batch)).status_code == 200

    [stored] = await stored_spans(session_factory)
    # 1000 input * 100 + 500 output * 200, per million tokens
    assert stored.cost_usd == Decimal("0.20000000")
    assert stored.pricing_version == f"override:{override_id}"
    assert stored.provider == span_provider
