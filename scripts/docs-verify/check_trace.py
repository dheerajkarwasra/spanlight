#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Print the newest trace of the docs-verify project (or one trace) as a single JSON object.

    uv run --script scripts/docs-verify/check_trace.py [--trace-id ID] [--wait 15]

It reads ``.local/docs-verify.env`` (written by ``bootstrap.py``), asks the API through the
personal access token, and waits up to ``--wait`` seconds for a trace to arrive. Exit code 1
means none did; exit code 2 means the API refused or failed (a bad token, a server error), which
is reported at once instead of being waited out.

Only traces that started at or after ``--since`` count; by default that is two minutes before
this command starts, so an old trace is never mistaken for a new one. The trace is printed once
its span count has stopped growing (two polls in a row agree), because spans of one trace can
arrive in more than one batch. ``--trace-id`` checks one trace and ignores ``--since``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

# Without --since, only traces that started this recently count, so an old trace is never
# mistaken for the one a sample just sent. Take a timestamp before the sample for full safety.
DEFAULT_LOOKBACK_SECONDS = 120
POLL_SECONDS = 1.0
SETTLE_GRACE = 10.0

ENV_FILE = Path(__file__).resolve().parents[2] / ".local" / "docs-verify.env"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def summarize(trace: dict[str, Any]) -> dict[str, Any]:
    """The fields a documentation sample promises, from the API's trace detail."""
    return {
        "trace_id": trace["trace_id"],
        "name": trace["name"],
        "environment": trace["environment"],
        "release": trace["release"],
        "user_id": trace["external_user_id"],
        "session_id": trace["session_id"],
        "total_cost": trace["cost_usd"],
        "spans": [
            {
                "name": span["name"],
                "kind": span["kind"],
                "provider": span["provider"],
                "model": span["model"],
                "input_tokens": span["input_tokens"],
                "output_tokens": span["output_tokens"],
                "cached_tokens": span["cached_tokens"],
                "cost": span["cost_usd"],
                "status": span["status"],
                "has_input": span["input"] is not None,
                "has_output": span["output"] is not None,
                "finish_reason": span["finish_reason"],
                "attributes": sorted(span["attributes"]),
            }
            for span in trace["spans"]
        ],
    }


class CheckError(RuntimeError):
    """The API answered in a way that retrying will not fix."""


def _checked(response: httpx.Response, *, missing_ok: bool) -> Any:
    """The JSON body, ``None`` for a 404 that means "not there yet", or a ``CheckError``."""
    if response.status_code == 200:
        return response.json()
    if response.status_code == 404 and missing_ok:
        return None
    hint = (
        " (is the personal access token in the env file still valid?)"
        if (response.status_code in (401, 403))
        else ""
    )
    raise CheckError(
        f"{response.request.method} {response.request.url.path} answered "
        f"{response.status_code}{hint}"
    )


def fetch(client: httpx.Client, project_id: str, trace_id: str | None, since: datetime) -> Any:
    """The trace detail, or ``None`` if there is no matching trace yet."""
    base = f"/api/v1/projects/{project_id}/traces"
    if trace_id:
        return _checked(client.get(f"{base}/{trace_id}"), missing_ok=True)
    until = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    params = {"from": since.isoformat(), "to": until, "limit": 1}
    page = _checked(client.get(base, params=params), missing_ok=False)
    if not page["items"]:
        return None
    return _checked(client.get(f"{base}/{page['items'][0]['trace_id']}"), missing_ok=True)


def wait_for_trace(
    client: httpx.Client,
    project_id: str,
    trace_id: str | None,
    since: datetime,
    wait: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Poll until a trace has arrived and its span count has stopped growing.

    Spans of one trace may be delivered in several batches, so the first sighting of a trace can
    be partial. The trace is returned after two polls in a row saw the same trace with the same
    number of spans. ``None`` means no trace appeared within ``wait`` seconds. After a trace has
    appeared, settling may take up to ``SETTLE_GRACE`` seconds past the deadline.
    """
    deadline = clock() + wait
    seen: tuple[str, int] | None = None
    latest: dict[str, Any] | None = None
    while True:
        trace = fetch(client, project_id, trace_id, since)
        if trace is not None:
            latest = trace
            signature = (trace["trace_id"], len(trace["spans"]))
            if signature == seen:
                return trace
            seen = signature
        elif latest is None and clock() >= deadline:
            return None
        if latest is not None and clock() >= deadline + SETTLE_GRACE:
            return latest
        sleep(POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trace-id", help="check this trace instead of the newest one")
    parser.add_argument("--wait", type=float, default=15, help="seconds to wait for a trace")
    parser.add_argument(
        "--since",
        help=(
            "ISO timestamp; ignore traces that started earlier. Default: "
            f"{DEFAULT_LOOKBACK_SECONDS} seconds before this command starts"
        ),
    )
    args = parser.parse_args()
    env = read_env(ENV_FILE)
    since = (
        datetime.fromisoformat(args.since).astimezone(UTC)
        if args.since
        else datetime.now(UTC) - timedelta(seconds=DEFAULT_LOOKBACK_SECONDS)
    )
    client = httpx.Client(
        base_url=env["SPANLIGHT_HOST"],
        headers={"Authorization": f"Bearer {env['SPANLIGHT_PAT']}"},
        timeout=30,
    )
    try:
        trace = wait_for_trace(client, env["SPANLIGHT_PROJECT_ID"], args.trace_id, since, args.wait)
    except (CheckError, httpx.HTTPError) as error:
        print(f"check_trace failed: {error}", file=sys.stderr)
        return 2
    if trace is None:
        print("no trace arrived", file=sys.stderr)
        return 1
    print(json.dumps(summarize(trace), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
