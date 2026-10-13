"""A `pg_dump`, `pg_restore` or `psql` child process, and how to stop it safely."""

import asyncio
import contextlib

import structlog

logger = structlog.get_logger(__name__)

# How much of the tool's stderr is kept for the error message.
STDERR_TAIL_BYTES = 2048
CHUNK_SIZE = 1024 * 1024
# How long stopping a child may take after it was killed.
STOP_TIMEOUT_SECONDS = 5.0


class ChildProcess:
    """A child process whose stderr is drained as it runs, keeping the last bytes of it.

    Draining matters: a child that fills the stderr pipe while nobody reads it blocks forever.
    """

    def __init__(self, process: asyncio.subprocess.Process, secret: str | None) -> None:
        self.process = process
        self._secret = secret
        self._tail = bytearray()
        self._drain = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.process.stderr is not None  # noqa: S101 - spawned with stderr=PIPE
        while chunk := await self.process.stderr.read(4096):
            self._tail += chunk
            del self._tail[:-STDERR_TAIL_BYTES]

    async def finish(self) -> tuple[int, str]:
        """Wait for exit; the status and the stderr tail, with the password masked."""
        code = await self.process.wait()
        await self._drain
        tail = self._tail.decode("utf-8", errors="replace").strip()
        if self._secret:
            tail = tail.replace(self._secret, "***")
        return code, tail

    async def stop(self) -> None:
        """Kill the child if it is still running and reap it. Safe to call twice.

        Since Python 3.12 `Process.wait()` returns only once the child's pipes are closed, and a
        pipe nobody reads never reaches end of file: a killed `pg_dump` whose output is still
        unread would make `wait()` hang. So the output is read and thrown away until it ends, and
        the whole step has a deadline, so stopping can never block the caller (or a cancellation).
        """
        process = self.process
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        try:
            async with asyncio.timeout(STOP_TIMEOUT_SECONDS):
                await asyncio.gather(discard_output(process.stdout), process.wait())
        except TimeoutError:
            logger.warning("subprocess.stop_timed_out", pid=process.pid)
        except Exception:
            logger.warning("subprocess.stop_failed", pid=process.pid, exc_info=True)
        self._drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain


async def discard_output(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(CHUNK_SIZE):
        pass
