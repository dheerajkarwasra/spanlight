"""Backup storage uses explicit, independent credentials and a separate client cache."""

from threading import Lock

from app.config import Settings
from app.storage.object_store import ObjectStore

_cache_lock = Lock()
_cached: tuple[Settings, ObjectStore] | None = None


def get_backup_object_store(settings: Settings) -> ObjectStore | None:
    """Return backup storage only when the explicit backup credential trio is complete."""
    global _cached  # noqa: PLW0603 - process-wide identity cache
    if not settings.is_backup_storage_configured:
        return None
    with _cache_lock:
        if _cached is None or _cached[0] is not settings:
            from app.storage.s3 import S3ObjectStore, _build_client  # noqa: PLC0415

            assert settings.backup_s3_bucket is not None  # noqa: S101 - validated settings
            assert settings.backup_s3_access_key is not None  # noqa: S101
            assert settings.backup_s3_secret_key is not None  # noqa: S101
            client = _build_client(
                settings.backup_s3_endpoint,
                settings.backup_s3_access_key.get_secret_value(),
                settings.backup_s3_secret_key.get_secret_value(),
                settings.backup_s3_region,
                settings.backup_s3_force_path_style,
            )
            store = S3ObjectStore(client, settings.backup_s3_bucket, sse=settings.backup_s3_sse)
            _cached = (settings, store)
        return _cached[1]
