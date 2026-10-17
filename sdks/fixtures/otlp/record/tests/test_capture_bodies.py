"""`capture_bodies.py` is owner-run; here it only ever talks to the fake provider."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from fake_provider import RESPONSES_DIR, ServerHandle, serve

RECORD_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def provider() -> Iterator[ServerHandle]:
    handle = serve(RESPONSES_DIR)
    yield handle
    handle.close()


def _run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and k != "CI"}
    return subprocess.run(
        [sys.executable, "capture_bodies.py", *args],
        cwd=RECORD_DIR,
        env={**clean, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_dry_run_plans_without_calling_or_writing(tmp_path: Path) -> None:
    result = _run("--dry-run", "--max-usd", "0.05", "--openai", "--out", str(tmp_path))

    assert result.returncode == 0, result.stderr
    for scenario in (
        "messages_cache_write",
        "messages_cache_read",
        "messages_stream",
        "embeddings",
    ):
        assert scenario in result.stdout
    assert "chat_tools" in result.stdout
    assert "worst case" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_records_bodies_verbatim_from_the_fake_provider(
    provider: ServerHandle, tmp_path: Path
) -> None:
    result = _run(
        "--max-usd",
        "0.05",
        "--openai",
        "--scenarios",
        "chat,chat_stream",
        "--out",
        str(tmp_path),
        "--openai-url",
        provider.url,
        OPENAI_API_KEY="sk-fixture",
    )

    assert result.returncode == 0, result.stderr
    openai_dir = tmp_path / "openai"
    for name in ("chat.json", "chat_stream.sse"):
        assert (openai_dir / name).read_bytes() == (RESPONSES_DIR / "openai" / name).read_bytes()
        provenance = (openai_dir / name).with_suffix(".provenance.txt").read_text()
        assert "recorded " in provenance
        assert "gpt-4.1-mini" in provenance
    spent = float(result.stdout.rsplit("spent ", 1)[1].split()[0])
    assert spent > 0


def test_stops_before_a_call_that_would_pass_the_cap(
    provider: ServerHandle, tmp_path: Path
) -> None:
    result = _run(
        "--max-usd",
        "0.0000001",
        "--openai",
        "--scenarios",
        "chat",
        "--out",
        str(tmp_path),
        "--openai-url",
        provider.url,
        OPENAI_API_KEY="sk-fixture",
    )

    assert result.returncode == 1
    assert "cap" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_refuses_to_run_in_ci(tmp_path: Path) -> None:
    result = _run("--dry-run", "--max-usd", "0.05", "--out", str(tmp_path), CI="true")

    assert result.returncode == 1
    assert "CI" in result.stderr


def _anthropic_run(
    responses_dir: Path, out: Path, body: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    (responses_dir / "anthropic").mkdir()
    (responses_dir / "anthropic" / "messages_cache_write.json").write_text(json.dumps(body))
    fake = serve(responses_dir)
    try:
        return _run(
            "--max-usd",
            "0.05",
            "--scenarios",
            "messages_cache_write",
            "--out",
            str(out),
            "--anthropic-url",
            fake.url,
            ANTHROPIC_API_KEY="sk-fixture",
        )
    finally:
        fake.close()


def test_cache_write_is_saved_only_with_cache_creation_tokens(tmp_path: Path) -> None:
    responses, out = tmp_path / "responses", tmp_path / "out"
    responses.mkdir()
    usage = {"input_tokens": 19, "output_tokens": 5, "cache_creation_input_tokens": 0}
    usage["cache_read_input_tokens"] = 6404

    result = _anthropic_run(responses, out, {"id": "msg_test", "usage": usage})

    assert result.returncode == 1
    assert "cache_creation_input_tokens" in result.stderr
    assert "6404" in result.stderr
    assert not out.exists()


def test_cache_write_with_cache_creation_tokens_is_saved(tmp_path: Path) -> None:
    responses, out = tmp_path / "responses", tmp_path / "out"
    responses.mkdir()
    usage = {"input_tokens": 19, "output_tokens": 5, "cache_creation_input_tokens": 6404}

    result = _anthropic_run(responses, out, {"id": "msg_test", "usage": usage})

    assert result.returncode == 0, result.stderr
    assert (
        json.loads((out / "anthropic" / "messages_cache_write.json").read_text())["usage"] == usage
    )


def test_bad_max_usd_is_an_argument_error(tmp_path: Path) -> None:
    for value in ("abc", "0", "1"):
        result = _run("--dry-run", "--max-usd", value, "--out", str(tmp_path))
        assert result.returncode == 2
        assert "max-usd" in result.stderr
        assert "Traceback" not in result.stderr
