"""Settings whose blank value means the default."""

from app.config import Settings


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
