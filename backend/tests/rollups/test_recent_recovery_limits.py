"""Recovery uses one wall-clock budget and reports only committed prefix progress."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest

from app.rollups.compute import floor_hour
from app.rollups.recovery import recover_recent
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects
from tests.rollups.recent_support import admit_at, state

pytestmark = pytest.mark.serial


async def test_recovery_stops_starting_chunks_before_thirty_seconds(
    session_factory: SessionFactory, app_database_url: str
) -> None:
    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(days=20)
    await admit_at(session_factory, project, start)
    await execute(
        session_factory,
        """CREATE TABLE recovery_start_probe(started timestamptz);
      CREATE FUNCTION slow_recovery() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.next_closed_hour<>OLD.next_closed_hour THEN
      INSERT INTO recovery_start_probe VALUES(transaction_timestamp()); PERFORM pg_sleep(.55);
      END IF; RETURN NEW; END $$;
      CREATE TRIGGER slow_recovery BEFORE UPDATE ON aggregate_job_progress
      FOR EACH ROW EXECUTE FUNCTION slow_recovery()""",
    )
    entries = []
    frames = []
    handler = None

    def observe(frame, event, arg):
        nonlocal handler
        if event != "call" or frame in frames:
            return
        if frame.f_code.co_name == "resume_recent":
            handler = frame
            frames.append(frame)
        if frame.f_code.co_name == "settle_recent":
            frames.append(frame)
            if handler is not None:
                entries.append(asyncio.get_running_loop().time() - handler.f_locals["started"])

    previous = sys.getprofile()
    try:
        [clock] = await fetch(session_factory, "SELECT clock_timestamp() started")
        started = monotonic()
        sys.setprofile(observe)
        result = await recover_recent(app_database_url, project, start, start + timedelta(days=15))
        sys.setprofile(previous)
        elapsed = monotonic() - started
        assert result.status == "incomplete" and 40 < result.chunks < 65
        assert result.cursor == start + timedelta(hours=3 * result.chunks)
        assert (await state(session_factory, project))["next_closed_hour"] == result.cursor
        probes = await fetch(session_factory, "SELECT * FROM recovery_start_probe ORDER BY started")
        assert len(probes) == result.chunks
        assert (probes[-1]["started"] - clock["started"]).total_seconds() < 30.2
        assert entries and all(age < 30 for age in entries)
        assert 30 <= elapsed < 32
        print("RECOVERY_MONOTONIC_STARTS", entries)
        print("CALIBRATION recovery_seconds=", elapsed, "committed_chunks=", result.chunks)
    finally:
        sys.setprofile(previous)
        await execute(
            session_factory,
            "DROP TRIGGER slow_recovery ON aggregate_job_progress; "
            "DROP FUNCTION slow_recovery(); DROP TABLE recovery_start_probe",
        )
