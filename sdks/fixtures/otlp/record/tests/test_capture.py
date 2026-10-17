"""The capture server stores every OTLP export verbatim and answers `{}`."""

import subprocess
import sys
from pathlib import Path

import httpx

from capture import serve
from test_fake_provider import raw_status

RECORD_DIR = Path(__file__).resolve().parent.parent


def test_capture_stores_body_and_content_type() -> None:
    handle = serve()
    try:
        httpx.post(
            handle.url + "/v1/otlp/traces",
            content=b"\x0a\x03abc",
            headers={"content-type": "application/x-protobuf"},
        )
        httpx.post(handle.url + "/v1/otlp/traces", json={"resourceSpans": []})
    finally:
        handle.close()

    assert handle.exports == [
        ("application/x-protobuf", b"\x0a\x03abc"),
        ("application/json", b'{"resourceSpans":[]}'),
    ]


def test_capture_answers_empty_json() -> None:
    handle = serve()
    try:
        response = httpx.post(handle.url + "/v1/otlp/traces", content=b"x")
        other = httpx.post(handle.url + "/v1/metrics", content=b"x")
    finally:
        handle.close()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {}
    assert other.status_code == 404


def test_capture_writes_exports_to_out_dir(tmp_path: Path) -> None:
    handle = serve(out_dir=tmp_path)
    try:
        httpx.post(
            handle.url + "/v1/otlp/traces",
            content=b"pb",
            headers={"content-type": "application/x-protobuf"},
        )
        httpx.post(handle.url + "/v1/otlp/traces", json={})
    finally:
        handle.close()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["0001.pb", "0002.json"]
    assert (tmp_path / "0001.pb").read_bytes() == b"pb"


def test_main_prints_the_bound_port(tmp_path: Path) -> None:
    with subprocess.Popen(
        [sys.executable, "capture.py", "--out", str(tmp_path)],
        cwd=RECORD_DIR,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            port = int(process.stdout.readline())
            response = httpx.post(f"http://127.0.0.1:{port}/v1/otlp/traces", content=b"pb")
            assert response.json() == {}
            assert (tmp_path / "0001.pb").read_bytes() == b"pb"
        finally:
            process.kill()


def test_bounded_body(tmp_path: Path) -> None:
    handle = serve(out_dir=tmp_path)
    try:
        assert raw_status(handle.port, "/v1/otlp/traces", str(6 * 1024 * 1024)) == 413
        assert raw_status(handle.port, "/v1/otlp/traces", "many") == 400
    finally:
        handle.close()
    assert handle.exports == []
    assert list(tmp_path.iterdir()) == []
