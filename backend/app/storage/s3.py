"""An `ObjectStore` on any S3-compatible service (AWS S3, MinIO, R2, ...).

boto3 is synchronous, so every call that touches the network runs in a worker thread.
"""

import asyncio
import builtins
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import boto3
import structlog
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.core.thread_ownership import owned_thread
from app.storage.object_store import ObjectInfo

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import CompletedPartTypeDef

logger = structlog.get_logger(__name__)

# Multipart parts are at least this big (S3 refuses parts under 5 MiB, except the last one), so
# a body that fits in one part is sent with a single PUT.
PART_SIZE = 8 * 1024 * 1024
# How much `open` reads from the network at a time.
READ_CHUNK_SIZE = 1024 * 1024
# AWS signs against a region; services that ignore it (MinIO) accept any value.
DEFAULT_REGION = "us-east-1"


def _build_client(
    endpoint: str | None,
    access_key: str,
    secret_key: str,
    region: str | None,
    force_path_style: bool,
) -> "S3Client":
    # A Session per client: sessions are not thread-safe, clients are.
    return boto3.Session().client(
        "s3",
        endpoint_url=endpoint,
        region_name=region or DEFAULT_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if force_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
            # Recent botocore versions add checksum headers to every request, which many
            # S3-compatible services reject. Only send them where the API requires them.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def attachment_disposition(key: str) -> str:
    """`attachment; filename="<last part of the key>"`, the name limited to `[A-Za-z0-9._-]`."""
    name = _UNSAFE_FILENAME_CHARS.sub("_", key.rsplit("/", 1)[-1]) or "download"
    return f'attachment; filename="{name}"'


class S3ObjectStore:
    def __init__(
        self,
        client: "S3Client",
        bucket: str,
        presign_client: "S3Client | None" = None,
        *,
        sse: str | None = None,
    ) -> None:
        # One client per store: boto3 clients are thread-safe, sessions are not.
        self._client = client
        self._bucket = bucket
        # Signs download links against the address browsers use; it never makes a request.
        self._presign_client = presign_client or client
        # `ServerSideEncryption` sent with every new object (a single PUT or a multipart upload).
        self._sse: dict[str, Any] = {"ServerSideEncryption": sse} if sse else {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3ObjectStore":
        if (
            settings.s3_bucket is None
            or settings.s3_access_key is None
            or settings.s3_secret_key is None
        ):
            raise ValueError("Object storage requires S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY")
        access_key = settings.s3_access_key.get_secret_value()
        secret_key = settings.s3_secret_key.get_secret_value()
        client = _build_client(
            settings.s3_endpoint,
            access_key,
            secret_key,
            settings.s3_region,
            settings.s3_force_path_style,
        )
        presign_client = None
        if settings.s3_public_endpoint not in (None, settings.s3_endpoint):
            presign_client = _build_client(
                settings.s3_public_endpoint,
                access_key,
                secret_key,
                settings.s3_region,
                settings.s3_force_path_style,
            )
        return cls(client, settings.s3_bucket, presign_client)

    async def put(self, key: str, body: bytes | AsyncIterator[bytes], content_type: str) -> None:
        if isinstance(body, bytes | bytearray | memoryview):
            await self._put_bytes(key, bytes(body), content_type)
        else:
            await self._put_stream(key, body, content_type)

    async def _put_bytes(self, key: str, body: bytes, content_type: str) -> None:
        if len(body) <= PART_SIZE:
            await self._put_object(key, body, content_type)
            return
        upload_id = await self._start_multipart(key, content_type)
        try:
            parts = [
                await self._upload_part(key, upload_id, number, body[start : start + PART_SIZE])
                for number, start in enumerate(range(0, len(body), PART_SIZE), start=1)
            ]
            await self._complete_multipart(key, upload_id, parts)
        except BaseException:
            await self._abort_multipart(key, upload_id)
            raise

    async def _put_stream(self, key: str, body: AsyncIterator[bytes], content_type: str) -> None:
        buffer = bytearray()
        upload_id: str | None = None
        parts: list[CompletedPartTypeDef] = []
        try:
            async for chunk in body:
                buffer += chunk
                while len(buffer) >= PART_SIZE:
                    if upload_id is None:
                        upload_id = await self._start_multipart(key, content_type)
                    part = bytes(buffer[:PART_SIZE])
                    del buffer[:PART_SIZE]
                    parts.append(await self._upload_part(key, upload_id, len(parts) + 1, part))
            if upload_id is None:
                # The whole body fit in one part (or was empty): a single PUT is enough.
                await self._put_object(key, bytes(buffer), content_type)
                return
            if buffer:
                parts.append(await self._upload_part(key, upload_id, len(parts) + 1, bytes(buffer)))
            await self._complete_multipart(key, upload_id, parts)
        except BaseException:
            if upload_id is not None:
                await self._abort_multipart(key, upload_id)
            raise

    async def open(self, key: str) -> AsyncIterator[bytes]:
        acquisition = asyncio.create_task(
            asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        )
        try:
            response = await owned_thread(acquisition)
        except asyncio.CancelledError:
            if not acquisition.cancelled() and acquisition.exception() is None:
                await owned_thread(asyncio.to_thread(acquisition.result()["Body"].close))
            raise
        stream = response["Body"]
        try:
            while chunk := await owned_thread(asyncio.to_thread(stream.read, READ_CHUNK_SIZE)):
                yield chunk
        finally:
            await owned_thread(asyncio.to_thread(stream.close))

    async def delete(self, key: str) -> None:
        await owned_thread(
            asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
        )

    async def list(self, prefix: str) -> list[ObjectInfo]:
        return await owned_thread(asyncio.to_thread(self._list_sync, prefix))

    def presigned_get_url(self, key: str, expires_in: int) -> str:
        # Signing is local arithmetic with no network call, so it needs no thread. The link asks
        # the store to answer with `Content-Disposition: attachment`, so a browser saves the file
        # instead of rendering it (or an error page) in the dashboard's tab. The header is signed
        # into the URL, so it also applies to objects stored before this was added.
        return self._presign_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": attachment_disposition(key),
            },
            ExpiresIn=expires_in,
        )

    def _list_sync(self, prefix: str) -> builtins.list[ObjectInfo]:
        paginator = self._client.get_paginator("list_objects_v2")
        return [
            ObjectInfo(key=item["Key"], size=item["Size"], last_modified=item["LastModified"])
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix)
            for item in page.get("Contents", [])
        ]

    async def _put_object(self, key: str, body: bytes, content_type: str) -> None:
        await owned_thread(
            asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                **self._sse,
            )
        )

    async def _start_multipart(self, key: str, content_type: str) -> str:
        acquisition = asyncio.create_task(
            asyncio.to_thread(
                self._client.create_multipart_upload,
                Bucket=self._bucket,
                Key=key,
                ContentType=content_type,
                **self._sse,
            )
        )
        try:
            response = await owned_thread(acquisition)
        except asyncio.CancelledError:
            if not acquisition.cancelled() and acquisition.exception() is None:
                await self._abort_multipart(key, acquisition.result()["UploadId"])
            raise
        return response["UploadId"]

    async def _upload_part(
        self, key: str, upload_id: str, number: int, body: bytes
    ) -> "CompletedPartTypeDef":
        response = await owned_thread(
            asyncio.to_thread(
                self._client.upload_part,
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=number,
                Body=body,
            )
        )
        return {"ETag": response["ETag"], "PartNumber": number}

    async def _complete_multipart(
        self, key: str, upload_id: str, parts: "builtins.list[CompletedPartTypeDef]"
    ) -> None:
        await owned_thread(
            asyncio.to_thread(
                self._client.complete_multipart_upload,
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        )

    async def _abort_multipart(self, key: str, upload_id: str) -> None:
        # Runs while another error is propagating, so it must not replace that error. Parts left
        # behind cost storage until the bucket's lifecycle rule removes them, hence the warning.
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key, "UploadId": upload_id}
        try:
            await owned_thread(asyncio.to_thread(self._client.abort_multipart_upload, **kwargs))
        except (BotoCoreError, ClientError):
            logger.warning("object_store.abort_multipart_failed", key=key, exc_info=True)
