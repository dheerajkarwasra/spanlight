"""Independent backup/export settings fail closed without credential fallback."""

from itertools import product

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.backups.storage import get_backup_object_store
from app.config import Settings


def test_export_credentials_do_not_enable_backup_storage() -> None:
    settings = Settings(
        _env_file=None,
        s3_bucket="exports",
        s3_access_key="export-access",
        s3_secret_key="export-secret",
    )
    assert settings.is_object_storage_configured
    assert not settings.is_backup_storage_configured


def test_backup_credentials_do_not_enable_exports() -> None:
    settings = Settings(
        _env_file=None,
        backup_s3_bucket="backups",
        backup_s3_access_key="upload-access",
        backup_s3_secret_key="upload-secret",
    )
    assert settings.is_backup_storage_configured
    assert not settings.is_object_storage_configured


@pytest.mark.parametrize(
    ("bucket", "access", "secret"),
    [combination for combination in product((False, True), repeat=3) if 0 < sum(combination) < 3],
)
def test_partial_backup_credentials_fail_closed(bucket: bool, access: bool, secret: bool) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            backup_s3_bucket="backups" if bucket else None,
            backup_s3_access_key="upload-access" if access else None,
            backup_s3_secret_key="upload-secret" if secret else None,
        )


def test_backup_factory_never_uses_generic_or_ambient_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
    settings = Settings(
        _env_file=None,
        s3_bucket="exports",
        s3_access_key="generic-access",
        s3_secret_key="generic-secret",
    )
    assert get_backup_object_store(settings) is None


@given(st.tuples(st.booleans(), st.booleans(), st.booleans()))
def test_backup_credential_combinations_are_all_or_none(present: tuple[bool, bool, bool]) -> None:
    values = dict(
        zip(
            ("backup_s3_bucket", "backup_s3_access_key", "backup_s3_secret_key"),
            ("configured" if value else "" for value in present),
            strict=True,
        )
    )
    if any(present) and not all(present):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **values)
    else:
        settings = Settings(_env_file=None, **values)
        assert settings.is_backup_storage_configured == all(present)
        assert not settings.is_object_storage_configured
