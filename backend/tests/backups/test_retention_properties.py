"""Retention invariants and explicit policy boundaries without a store substitute."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from app.backups.retention import backup_key, prune_backups
from app.storage.object_store import ObjectInfo

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def test_exact_daily_and_sunday_boundaries() -> None:
    moments = [
        NOW,
        datetime(2026, 10, 16, 12, tzinfo=UTC),  # Fourteenth UTC day.
        datetime(2026, 10, 15, 12, tzinfo=UTC),  # First expired daily day, not Sunday.
        datetime(2026, 9, 6, 12, tzinfo=UTC),  # Eighth Sunday.
        datetime(2026, 8, 30, 12, tzinfo=UTC),  # Ninth Sunday.
    ]
    objects = [ObjectInfo(backup_key(moment), 1, moment) for moment in moments]
    assert prune_backups(objects, NOW) == sorted([backup_key(moments[2]), backup_key(moments[4])])


@given(st.lists(st.integers(min_value=-1000, max_value=1000), min_size=1, unique=True))
def test_order_invariance_and_preserved_newest_future_unknown(offsets: list[int]) -> None:
    moments = [NOW + timedelta(days=offset) for offset in offsets]
    objects = [ObjectInfo(backup_key(moment), 1, moment) for moment in moments]
    objects.append(ObjectInfo("backups/operator.keep", 1, NOW))
    selected = prune_backups(objects, NOW)
    assert selected == prune_backups(reversed(objects), NOW)
    assert "backups/operator.keep" not in selected
    assert backup_key(max(moments)) not in selected
    for moment in moments:
        if moment.date() > NOW.date():
            assert backup_key(moment) not in selected
