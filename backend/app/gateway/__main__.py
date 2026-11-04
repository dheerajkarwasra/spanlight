"""Run the standalone gateway: `python -m app.gateway`.

Serves `app.main.create_gateway_app` (`/gw/v1/*`, `/health/*`, `/metrics`) with uvicorn, from
the same image as the api. Run the api with `GATEWAY_MODE=standalone` next to it and point
Caddy's `GATEWAY_UPSTREAM` here. `PORT` (default 8001) and `GATEWAY_HOST` (default all
interfaces, as in the container) choose where it listens.
"""

from app.server import run_application

DEFAULT_PORT = 8001


def main() -> None:
    run_application(gateway=True)


if __name__ == "__main__":
    main()
