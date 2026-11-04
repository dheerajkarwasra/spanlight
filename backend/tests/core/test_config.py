"""Settings whose blank value means the default."""

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.config import DEV_SECRET_KEY, REQUEST_POOL_SIZE, Settings


def test_gateway_record_concurrency_leaves_the_request_pool_room() -> None:
    # Gateway span writes run on the request pool. With more writers than the pool's base size,
    # a convoy of writes (they serialize on one dirty-hour row per project) takes the connections
    # requests need, and every gateway call stalls behind the background writes. The default
    # stays under the pool, and an environment value over it is refused at startup.
    assert Settings(_env_file=None).gateway_record_concurrency <= REQUEST_POOL_SIZE
    with pytest.raises(ValidationError, match="gateway_record_concurrency"):
        Settings(_env_file=None, gateway_record_concurrency=REQUEST_POOL_SIZE + 1)


def test_provider_status_polling_enabled_blank_is_the_default() -> None:
    # `PROVIDER_STATUS_POLLING_ENABLED=` from a Compose file means "the default": polling on.
    blank = Settings(_env_file=None, provider_status_polling_enabled="")
    assert blank.provider_status_polling_enabled is True
    assert Settings(_env_file=None).provider_status_polling_enabled is True
    off = Settings(_env_file=None, provider_status_polling_enabled="false")
    assert off.provider_status_polling_enabled is False


def test_attribution_enabled_blank_is_the_default() -> None:
    # `ATTRIBUTION_ENABLED=` from a Compose file means "the default": the refresh job runs.
    assert Settings(_env_file=None, attribution_enabled="").attribution_enabled is True
    assert Settings(_env_file=None).attribution_enabled is True
    assert Settings(_env_file=None, attribution_enabled="false").attribution_enabled is False


def test_rate_limit_deadlines_default_and_blank() -> None:
    settings = Settings(_env_file=None)
    assert settings.rate_limit_lock_timeout_ms == 100
    assert settings.rate_limit_statement_timeout_ms == 250
    blank = Settings(
        _env_file=None, rate_limit_lock_timeout_ms="", rate_limit_statement_timeout_ms=""
    )
    assert blank.rate_limit_lock_timeout_ms == 100
    assert blank.rate_limit_statement_timeout_ms == 250


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("name", ["rate_limit_lock_timeout_ms", "rate_limit_statement_timeout_ms"])
def test_rate_limit_deadlines_reject_nonpositive_nonfinite(name: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


@pytest.mark.parametrize("lock", [250, 251])
def test_rate_limit_deadlines_reject_invalid_order(lock: int) -> None:
    with pytest.raises(ValidationError, match="must be less than"):
        Settings(
            _env_file=None, rate_limit_lock_timeout_ms=lock, rate_limit_statement_timeout_ms=250
        )


@given(lock=st.integers(min_value=1, max_value=500), gap=st.integers(min_value=1, max_value=500))
@hypothesis_settings(max_examples=20)
def test_rate_limit_deadline_order_property(lock: int, gap: int) -> None:
    settings = Settings(
        _env_file=None,
        rate_limit_lock_timeout_ms=lock,
        rate_limit_statement_timeout_ms=lock + gap,
    )
    assert settings.rate_limit_lock_timeout_ms < settings.rate_limit_statement_timeout_ms
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            rate_limit_lock_timeout_ms=lock + gap,
            rate_limit_statement_timeout_ms=lock,
        )


@pytest.mark.parametrize(
    "base_url", ["https://spanlight.example.com", "http://spanlight.example.com", "http://10.0.0.5"]
)
def test_a_short_secret_key_is_refused_outside_local_development(base_url: str) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY must be at least 32 characters"):
        Settings(_env_file=None, app_base_url=base_url, secret_key="k" * 31)
    assert Settings(_env_file=None, app_base_url=base_url, secret_key="k" * 32)


@pytest.mark.parametrize(
    "base_url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"]
)
def test_a_short_secret_key_is_allowed_for_local_development(base_url: str) -> None:
    assert Settings(_env_file=None, app_base_url=base_url, secret_key="change-me")


@pytest.mark.parametrize(
    ("base_url", "refused"),
    [
        ("http://spanlight.example.com", True),
        ("http://10.0.0.5", True),
        ("https://localhost", True),
        ("http://localhost:8000", False),
        ("http://[::1]:8000", False),
    ],
)
def test_the_public_development_key_only_runs_locally(base_url: str, refused: bool) -> None:
    if refused:
        with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
            Settings(_env_file=None, app_base_url=base_url, secret_key=DEV_SECRET_KEY)
    else:
        assert Settings(_env_file=None, app_base_url=base_url, secret_key=DEV_SECRET_KEY)
