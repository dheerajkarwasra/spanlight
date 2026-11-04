"""Real S3 resource acquisitions cannot be discarded after a cancelled thread await."""

import asyncio
import threading
import uuid
from typing import Any

import pytest

from app.storage.s3 import PART_SIZE, S3ObjectStore
from tests.backups.test_isolated_storage import EXPORTS
from tests.backups.test_isolated_storage import (
    minio as minio,  # noqa: PLC0414 - pytest fixture re-export
)

pytestmark = pytest.mark.serial


async def test_cancelled_get_closes_the_acquired_streaming_body(minio: Any) -> None:
    _, _, client = minio
    key = f"shutdown/{uuid.uuid4().hex}"
    await asyncio.to_thread(client.put_object, Bucket=EXPORTS, Key=key, Body=b"test data")
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    bodies = []

    def held_response(parsed: Any, **kwargs: Any) -> None:
        bodies.append(parsed["Body"])
        loop.call_soon_threadsafe(started.set)
        release.wait()

    client.meta.events.register("after-call.s3.GetObject", held_response)
    stream = S3ObjectStore(client, EXPORTS).open(key)
    task = asyncio.create_task(anext(stream))
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel("first")
        await asyncio.sleep(0)
        task.cancel("second")
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError, match="first"):
            await task
        with bodies[0] as response:
            assert response.closed
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await stream.aclose()
        client.meta.events.unregister("after-call.s3.GetObject", held_response)
        await asyncio.to_thread(client.delete_object, Bucket=EXPORTS, Key=key)


async def test_cancelled_multipart_creation_aborts_the_known_upload(minio: Any) -> None:
    _, _, client = minio
    key = f"shutdown/{uuid.uuid4().hex}"
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    uploads = []

    def held_response(parsed: Any, **kwargs: Any) -> None:
        uploads.append(parsed["UploadId"])
        loop.call_soon_threadsafe(started.set)
        release.wait()

    client.meta.events.register("after-call.s3.CreateMultipartUpload", held_response)
    task = asyncio.create_task(
        S3ObjectStore(client, EXPORTS).put(key, b"x" * (PART_SIZE + 1), "text/plain")
    )
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(uploads) == 1
        remaining = await asyncio.to_thread(
            client.list_multipart_uploads, Bucket=EXPORTS, Prefix=key
        )
        assert remaining.get("Uploads", []) == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        client.meta.events.unregister("after-call.s3.CreateMultipartUpload", held_response)
