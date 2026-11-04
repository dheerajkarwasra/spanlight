"""A blocked real exporter must not be rejoined by interpreter atexit."""

import asyncio
import threading

from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from app.config import Settings
from app.core.tracing import configure_worker_tracing, shutdown_tracing
from app.db.session import create_engine
from app.process_shutdown import ProcessShutdown, ShutdownBudget, current


class HeldExporter(SpanExporter):
    def shutdown(self) -> None:
        threading.Event().wait()


async def main() -> None:
    settings = Settings(otel_exporter_otlp_endpoint="http://127.0.0.1:1", _env_file=None)
    engine = create_engine(settings.database_url)
    provider = configure_worker_tracing(settings, engine)
    assert provider is not None
    provider.add_span_processor(SimpleSpanProcessor(HeldExporter()))
    process = ProcessShutdown(ShutdownBudget(0.1, 0.01, 0.02, 0.02))
    current.set(process)
    process.begin()
    await shutdown_tracing(provider)
    assert not process.accounting_complete
    await engine.dispose()
    print("TRACING_RETURNED_INCOMPLETE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
