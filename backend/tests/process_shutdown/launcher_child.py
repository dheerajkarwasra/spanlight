"""Execute production module entrypoints with an HTTP-only inspection route in tests."""

import json
import os
import runpy
import sys
from typing import Any

import uvicorn
from fastapi import FastAPI, Request

from tests.process_shutdown.server_child import event


def main() -> None:
    data = json.loads(sys.stdin.readline())
    os.environ.update(data["environment"])
    if data["module"] == "app.jobs.worker":
        from app.jobs import worker_lifecycle  # noqa: PLC0415 - after environment setup

        original_worker = worker_lifecycle.run_worker

        async def observed_worker(*args: Any, **kwargs: Any) -> None:
            event("worker_owner", owns_executor=kwargs.get("owns_executor", False))
            await original_worker(*args, **kwargs)

        worker_lifecycle.run_worker = observed_worker
    original_run = uvicorn.Server.run

    def observed_run(self: Any, *args: Any, **kwargs: Any) -> Any:
        event("launcher_owner", owns_executor=self.owns_executor)
        return original_run(self, *args, **kwargs)

    uvicorn.Server.run = observed_run
    original = uvicorn.Config

    class ObservedConfig(original):
        def load(self) -> None:
            super().load()
            application = self.loaded_app
            while not isinstance(application, FastAPI):
                application = application.app

            @application.get("/launcher-probe")
            async def probe(request: Request) -> dict[str, object]:
                return {
                    "client": request.client.host if request.client else None,
                    "migration_present": "MIGRATION_DATABASE_URL" in os.environ,
                    "port": self.port,
                    "proxy_headers": self.proxy_headers,
                }

    uvicorn.Config = ObservedConfig
    event("port", port=int(os.environ["PORT"]))
    if data.get("host") is not None:
        from app.server import run_application  # noqa: PLC0415 - after test environment setup

        run_application(host=data["host"])
    else:
        runpy.run_module(data["module"], run_name="__main__")


if __name__ == "__main__":
    main()
