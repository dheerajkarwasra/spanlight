"""Backup uploads ask the store to encrypt at rest (BACKUP_S3_SSE); export uploads do not."""

from typing import Any

from botocore.stub import ANY, Stubber

from app.backups.storage import get_backup_object_store
from app.config import Settings
from app.storage.s3 import PART_SIZE, S3ObjectStore

BUCKET = "backups"
KEY = "backups/2026/11/01/spanlight.dump"
CONTENT_TYPE = "application/octet-stream"


def _backup_store(**overrides: Any) -> S3ObjectStore:
    settings = Settings(
        _env_file=None,
        backup_s3_bucket=BUCKET,
        backup_s3_access_key="upload-access",
        backup_s3_secret_key="upload-secret",
        **overrides,
    )
    store = get_backup_object_store(settings)
    assert isinstance(store, S3ObjectStore)
    return store


def _put_params(**sse: str) -> dict[str, Any]:
    return {"Bucket": BUCKET, "Key": KEY, "Body": b"dump", "ContentType": CONTENT_TYPE, **sse}


async def test_backup_upload_sends_sse_s3_by_default() -> None:
    store = _backup_store()
    with Stubber(store._client) as stub:
        stub.add_response("put_object", {}, _put_params(ServerSideEncryption="AES256"))
        await store.put(KEY, b"dump", CONTENT_TYPE)
        stub.assert_no_pending_responses()


async def test_backup_multipart_upload_sends_sse_on_create() -> None:
    store = _backup_store()
    with Stubber(store._client) as stub:
        stub.add_response(
            "create_multipart_upload",
            {"UploadId": "u1"},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "ContentType": CONTENT_TYPE,
                "ServerSideEncryption": "AES256",
            },
        )
        for number in (1, 2):
            stub.add_response(
                "upload_part",
                {"ETag": f'"{number}"'},
                {"Bucket": BUCKET, "Key": KEY, "UploadId": "u1", "PartNumber": number, "Body": ANY},
            )
        stub.add_response("complete_multipart_upload", {})
        await store.put(KEY, b"x" * (PART_SIZE + 1), CONTENT_TYPE)
        stub.assert_no_pending_responses()


async def test_blank_backup_sse_sends_no_header() -> None:
    store = _backup_store(backup_s3_sse="")
    with Stubber(store._client) as stub:
        stub.add_response("put_object", {}, _put_params())
        await store.put(KEY, b"dump", CONTENT_TYPE)
        stub.assert_no_pending_responses()


async def test_export_upload_is_unchanged() -> None:
    settings = Settings(
        _env_file=None,
        s3_bucket=BUCKET,
        s3_access_key="export-access",
        s3_secret_key="export-secret",
    )
    store = S3ObjectStore.from_settings(settings)
    with Stubber(store._client) as stub:
        stub.add_response("put_object", {}, _put_params())
        await store.put(KEY, b"dump", CONTENT_TYPE)
        stub.assert_no_pending_responses()
