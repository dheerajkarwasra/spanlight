"""Enqueue the rollup and detector jobs on the local dev database and wait for them.

The jobs are inserted straight into the `jobs` table of the dev Postgres container
(`DOCTOR_HARNESS_PG_CONTAINER`, default `idoc-test-pg`); the worker leases them like any
scheduled job. Apart from this and the status poll of
`provider_incident:with_incident` (`incident_feed.py`), the harness never touches the database.
The worker (`uv run python -m app.jobs.worker`) must be running.

    cd backend
    uv run python -m accuracy.doctor.trigger                  # rollup, detectors, then poll
    uv run python -m accuracy.doctor.trigger --rollup-only    # after --baseline
    uv run python -m accuracy.doctor.trigger --detectors-only
    uv run python -m accuracy.doctor.trigger --dry-run        # print the commands, run nothing

Polling: each job is watched through `SELECT status FROM jobs WHERE id = ...` until it is
`done` or `failed`; after `run_detectors` every doctor project is polled on
`GET /api/v1/projects/{id}/detector-runs` until a run newer than the trigger time appears.
"""

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime

from accuracy.doctor.common import PatApi, all_projects, doctor_state
from accuracy.setup import HOST

# The dev Postgres container; `DOCTOR_HARNESS_PG_CONTAINER` names another one.
CONTAINER = os.environ.get("DOCTOR_HARNESS_PG_CONTAINER", "").strip() or "idoc-test-pg"
DB = "spanlight"
PSQL = ["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-d", DB, "-At", "-c"]

INSERT_SQL = (
    "INSERT INTO jobs (kind, payload, max_attempts) VALUES ('{kind}','{{}}',1) RETURNING id"
)
STATUS_SQL = (
    "SELECT status || ' ' || coalesce(outcome, '') || ' ' || coalesce(last_error, '') "
    "FROM jobs WHERE id = {id}"
)


def command_line(sql: str) -> str:
    return " ".join(PSQL) + " " + json.dumps(sql)


def psql(sql: str) -> str:
    # A fixed argv (docker exec ... psql -c <constant SQL>); no shell, no outside input.
    completed = subprocess.run(  # noqa: S603
        [*PSQL, sql], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise SystemExit(f"psql failed ({completed.returncode}): {completed.stderr.strip()}")
    return completed.stdout.strip()


def enqueue(kind: str, *, dry_run: bool) -> int | None:
    sql = INSERT_SQL.format(kind=kind)
    print(f"$ {command_line(sql)}")
    if dry_run:
        return None
    job_id = int(psql(sql).splitlines()[0])
    print(f"  job {job_id} queued")
    return job_id


def wait_job(job_id: int, *, timeout_s: float) -> None:
    started = time.monotonic()
    while True:
        status = psql(STATUS_SQL.format(id=job_id))
        state = status.split(" ", 1)[0]
        if state in ("done", "failed"):
            print(f"  job {job_id}: {status.strip()}")
            if state == "failed":
                raise SystemExit(f"job {job_id} failed")
            return
        if time.monotonic() - started > timeout_s:
            raise SystemExit(f"job {job_id} still {state} after {timeout_s:.0f}s")
        time.sleep(3)


def wait_detector_runs(since: datetime, *, timeout_s: float) -> None:
    """Poll every doctor project until it has a detector run newer than `since`."""
    state = doctor_state()
    api = PatApi(state["pat"], HOST)
    projects = all_projects(state)
    pending = {name: entry["project_id"] for name, entry in projects.items()}
    started = time.monotonic()
    while pending:
        for name, project_id in list(pending.items()):
            runs = api.detector_runs(project_id, limit=20)
            newest = max((datetime.fromisoformat(r["ran_at"]) for r in runs), default=None)
            if newest is not None and newest > since:
                errors = [
                    r["detector"]
                    for r in runs
                    if r["error"] and datetime.fromisoformat(r["ran_at"]) > since
                ]
                print(
                    f"  {name:48s} ran {newest.isoformat(timespec='seconds')}"
                    + (f" errors={errors}" if errors else "")
                )
                del pending[name]
        if not pending:
            break
        if time.monotonic() - started > timeout_s:
            print(f"timed out; {len(pending)} projects without a new run: {sorted(pending)}")
            raise SystemExit(2)
        print(f"  waiting for {len(pending)} projects...")
        time.sleep(10)
    print("every doctor project has a detector run newer than the trigger")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rollup-only", action="store_true")
    parser.add_argument("--detectors-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait per step")
    args = parser.parse_args(argv)

    if not args.detectors_only:
        job = enqueue("rollup_hourly", dry_run=args.dry_run)
        if job is not None:
            wait_job(job, timeout_s=args.timeout)
    if not args.rollup_only:
        since = datetime.now(tz=UTC)
        job = enqueue("run_detectors", dry_run=args.dry_run)
        if job is not None:
            wait_job(job, timeout_s=args.timeout)
            wait_detector_runs(since, timeout_s=args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
