"""The handler for database data errors: a NUL in a request value is a 422, anything else a 500.

Ingestion strips NUL before it reaches the database, so this is the backstop for routes that do
not. Postgres reports a NUL in two ways: psycopg refuses it for a text parameter before sending
anything (no SQLSTATE), and the server refuses `\\u0000` inside a jsonb value (SQLSTATE 22P05).
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import install_error_handlers


@pytest.fixture
async def probe(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    """An app with the real error handlers and one route per statement under test."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    application = FastAPI()
    install_error_handlers(application)

    async def run(statement: str, value: str | None = None) -> dict[str, str]:
        async with factory() as db:
            await db.execute(text(statement), {"v": value} if value is not None else {})
        return {"ok": "yes"}

    @application.get("/text")
    async def text_parameter() -> dict[str, str]:
        return await run("SELECT CAST(:v AS text)", "a\x00b")

    @application.get("/jsonb")
    async def jsonb_parameter() -> dict[str, str]:
        return await run("SELECT CAST(:v AS jsonb)", '{"a": "\\u0000"}')

    @application.get("/other")
    async def other_data_error() -> dict[str, str]:
        return await run("SELECT CAST('not a number' AS integer)")

    @application.get("/untranslatable")
    async def untranslatable_character() -> dict[str, str]:
        # Also SQLSTATE 22P05, but about encodings: nothing the client sent is to blame.
        return await run("SELECT convert_to(CAST(:v AS text), 'LATIN1')", "\u20ac")

    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as http:
        yield http


@pytest.mark.parametrize("path", ["/text", "/jsonb"])
async def test_a_nul_character_reaching_the_database_is_a_422(
    probe: httpx.AsyncClient, path: str
) -> None:
    response = await probe.get(path)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_another_data_error_is_still_a_500(probe: httpx.AsyncClient) -> None:
    response = await probe.get("/other")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


async def test_an_untranslatable_character_that_is_not_nul_is_still_a_500(
    probe: httpx.AsyncClient,
) -> None:
    response = await probe.get("/untranslatable")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
