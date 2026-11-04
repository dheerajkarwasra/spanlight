"""The real tracing provider cannot spend a fresh timeout after the process deadline."""

import asyncio
import sys
import threading

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from app.core.tracing import shutdown_tracing
from app.process_shutdown import ProcessShutdown, ShutdownBudget, current

pytestmark = pytest.mark.serial


async def test_tracing_uses_remaining_process_time() -> None:
    release, finished = threading.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()

    class HeldExporter(SpanExporter):
        def shutdown(self) -> None:
            release.wait(1)
            loop.call_soon_threadsafe(finished.set)

    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(HeldExporter()))
    process = ProcessShutdown(ShutdownBudget(0.1, 0.01, 0.02, 0.02))
    token = current.set(process)
    process.begin()
    start = asyncio.get_running_loop().time()
    try:
        await shutdown_tracing(provider)
        assert asyncio.get_running_loop().time() - start < 0.3
        assert not process.accounting_complete
    finally:
        release.set()
        async with asyncio.timeout(1):
            await finished.wait()
        current.reset(token)


async def test_unfinished_exporter_does_not_block_interpreter_exit() -> None:
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.process_shutdown.tracing_child",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        # Hang detector: a rejoined exporter never exits; a clean exit took 5.2 s under load.
        async with asyncio.timeout(15):
            output, _ = await child.communicate()
        assert child.returncode == 0, output.decode()
        assert b"TRACING_RETURNED_INCOMPLETE" in output
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
