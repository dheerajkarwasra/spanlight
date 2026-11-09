"""Only the authorized marker reader may reference the cross-project function."""

from pathlib import Path

import pytest

from app.db.roles import APP_FUNCTIONS


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """These source checks do not use a database."""


def test_live_markers_is_granted_by_signature() -> None:
    assert "public.live_markers(uuid[])" in APP_FUNCTIONS


def test_only_the_marker_reader_references_live_markers() -> None:
    app = Path(__file__).resolve().parents[2] / "app"
    callers = set()
    for path in app.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path == app / "db" / "roles.py":
            # Provisioning declares the grant; it is not a marker read.
            source = source.replace('"public.live_markers(uuid[])",', "", 1)
        if "live_markers" in source:
            callers.add(path.relative_to(app).as_posix())
    assert callers <= {"live/markers.py"}
