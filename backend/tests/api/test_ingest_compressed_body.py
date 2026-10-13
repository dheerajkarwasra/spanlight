"""Compressed ingestion bodies that do not decompress whole are refused, never half-read."""

import gzip
import zlib
from typing import Any

import httpx
import pytest

from tests.helpers import Workspace, bearer, create_workspace


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    return await create_workspace(await browser_factory("owner@example.com"))


@pytest.mark.parametrize(
    ("encoding", "body"),
    [
        ("gzip", b""),
        ("deflate", b""),
        ("gzip", gzip.compress(b'{"spans":[]}')[:-3]),
        ("deflate", zlib.compress(b'{"spans":[]}')[:-3]),
    ],
    ids=["empty-gzip", "empty-deflate", "cut-gzip", "cut-deflate"],
)
async def test_an_empty_or_cut_compressed_body_is_a_400_bad_request(
    client: httpx.AsyncClient, workspace: Workspace, encoding: str, body: bytes
) -> None:
    response = await client.post(
        "/v1/traces",
        content=body,
        headers={
            **bearer(workspace.api_key),
            "Content-Type": "application/json",
            "Content-Encoding": encoding,
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"
    assert response.json()["detail"] == "The request body could not be decompressed."
