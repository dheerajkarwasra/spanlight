"""A real PostgreSQL TCP hop that can withhold rollback writes for cleanup tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit


@dataclass
class RollbackProxy:
    database_url: str
    client_url: str = ""
    hold_rollback: bool = False
    release: asyncio.Event = field(default_factory=asyncio.Event)
    rollback_seen: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        upstream_writer: asyncio.StreamWriter | None = None
        pumps: list[asyncio.Task[None]] = []
        try:
            parts = urlsplit(self.database_url)
            upstream_reader, upstream_writer = await asyncio.open_connection(
                parts.hostname, parts.port
            )

            async def forward(
                source: asyncio.StreamReader,
                destination: asyncio.StreamWriter,
                *,
                client: bool,
            ) -> None:
                tail = b""
                while data := await source.read(65536):
                    if client and self.hold_rollback and b"ROLLBACK" in tail + data:
                        self.rollback_seen.set()
                        await self.release.wait()
                    tail = (tail + data)[-8:]
                    destination.write(data)
                    await destination.drain()

            pumps = [
                asyncio.create_task(forward(reader, upstream_writer, client=True)),
                asyncio.create_task(forward(upstream_reader, writer, client=False)),
            ]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            if upstream_writer is not None:
                upstream_writer.close()
                with suppress(ConnectionError):
                    await upstream_writer.wait_closed()
            self.tasks.discard(task)


@asynccontextmanager
async def rollback_proxy(database_url: str) -> AsyncIterator[RollbackProxy]:
    proxy = RollbackProxy(database_url)
    server = await asyncio.start_server(proxy.connect, "127.0.0.1", 0)
    parts = urlsplit(database_url)
    port = server.sockets[0].getsockname()[1]
    credentials = parts.netloc.rsplit("@", 1)[0]
    proxied_url = urlunsplit(
        parts._replace(netloc=f"{credentials}@127.0.0.1:{port}", query="sslmode=disable")
    )
    try:
        proxy.client_url = proxied_url
        yield proxy
    finally:
        proxy.release.set()
        server.close()
        await server.wait_closed()
        for task in list(proxy.tasks):
            task.cancel()
        await asyncio.gather(*proxy.tasks, return_exceptions=True)
