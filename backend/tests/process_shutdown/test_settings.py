"""All process allocations are finite, positive and leave a real cancellation join window."""

import pytest
from pydantic import ValidationError

from app.config import Settings

pytestmark = pytest.mark.serial


@pytest.mark.parametrize(
    "field",
    [
        "shutdown_timeout_seconds",
        "shutdown_work_seconds",
        "shutdown_flush_seconds",
        "shutdown_close_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_shutdown_settings_reject_nonpositive_or_nonfinite(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_shutdown_settings_reject_no_transaction_join_reserve() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            shutdown_timeout_seconds=10,
            shutdown_work_seconds=5,
            shutdown_flush_seconds=3,
            shutdown_close_seconds=2,
        )


def test_shutdown_blank_values_use_calibrated_defaults() -> None:
    settings = Settings(
        _env_file=None,
        shutdown_timeout_seconds="",
        shutdown_work_seconds="",
        shutdown_flush_seconds="",
        shutdown_close_seconds="",
    )
    assert settings.shutdown_timeout_seconds == 60
    assert settings.shutdown_work_seconds == 50
    assert settings.shutdown_flush_seconds == 3
    assert settings.shutdown_close_seconds == 2
