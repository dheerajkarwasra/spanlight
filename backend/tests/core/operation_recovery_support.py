"""Real PostgreSQL faults and local HTTP instrumentation for operation recovery tests."""

import asyncio

import psycopg
import pytest

from tests.conftest import admin_database_url


@pytest.fixture
async def response_cache_fault(session_factory):
    admin_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    async with await psycopg.AsyncConnection.connect(admin_url, autocommit=True) as admin:
        await admin.execute("""
            CREATE FUNCTION fail_response_cache() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF nullif(current_setting('app.project_id', true), '') IS NULL THEN
                    RAISE EXCEPTION 'injected response-store failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER fail_response_cache BEFORE UPDATE ON idempotency_keys
            FOR EACH ROW EXECUTE FUNCTION fail_response_cache();
        """)
        try:
            yield
        finally:
            await admin.execute("DROP TRIGGER fail_response_cache ON idempotency_keys")
            await admin.execute("DROP FUNCTION fail_response_cache()")


@pytest.fixture
async def recording_http_provider():
    """A real local HTTP service records calls; its gate can hold a live request open."""
    entered = asyncio.Event()
    gate = asyncio.Event()
    requests: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            requests.append(headers)
            entered.set()
            await gate.wait()
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\n{"paid":true}'
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", entered, gate, requests
    finally:
        gate.set()
        server.close()
        await server.wait_closed()
