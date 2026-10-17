"""The fixture document (`../schema.json`) built from captured exports, and the `--check` diff."""

import base64
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

REPO = Path(__file__).resolve().parents[4]
PROTOBUF = "application/x-protobuf"


class Export(NamedTuple):
    scenario: str
    content_type: str
    body: bytes


class Recorder(NamedTuple):
    runtime: str
    otel_sdk: str
    loses: dict[str, list[str]]


def diff_expected(committed: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    """Field paths where the scenarios' `expected` sections differ, `scenarios[i]....: a != b`."""
    lines: list[str] = []
    _walk("scenarios", _expected_only(committed), _expected_only(fresh), lines)
    return lines


def _expected_only(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"name": s["name"], "expected": s["expected"]} for s in document["scenarios"]]


def _walk(path: str, a: Any, b: Any, lines: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(a.keys() | b.keys()):
            if key not in a or key not in b:
                lines.append(f"{path}.{key}: missing in {'committed' if key not in a else 'fresh'}")
            else:
                _walk(f"{path}.{key}", a[key], b[key], lines)
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            if i >= len(a) or i >= len(b):
                lines.append(f"{path}[{i}]: missing in {'committed' if i >= len(a) else 'fresh'}")
            else:
                _walk(f"{path}[{i}]", a[i], b[i], lines)
    elif a != b:
        lines.append(f"{path}: {a!r} != {b!r}")


def build_document(
    library: str, version: str, recorder: Recorder, exports: list[Export]
) -> dict[str, Any]:
    """The fixture file for one library and version, in the shape of `../schema.json`."""
    return {
        "library": library,
        "version": version,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recorder": {
            "runtime": recorder.runtime,
            "otel_sdk": recorder.otel_sdk,
            "harness": f"sdks/fixtures/otlp/record@{_git_sha()}",
        },
        "scenarios": [
            {
                "name": export.scenario,
                "export": {
                    "content_type": export.content_type,
                    "body_base64": base64.b64encode(export.body).decode(),
                },
                "preview": preview_for(export),
                "expected": {
                    **expected_for(export),
                    "loses": recorder.loses.get(export.scenario, []),
                },
            }
            for export in exports
        ],
    }


def preview_for(export: Export) -> dict[str, Any]:
    if export.content_type != PROTOBUF:
        return json.loads(export.body)
    message = ExportTraceServiceRequest()
    message.ParseFromString(export.body)
    return {"resourceSpans": [], **MessageToDict(message)}


def expected_for(export: Export) -> dict[str, Any]:
    """Run the backend's mapper on the bytes through `scripts/otlp_expected.py`."""
    with tempfile.NamedTemporaryFile(suffix=".bin") as file:
        file.write(export.body)
        file.flush()
        result = subprocess.run(
            [
                *("uv", "run", "--project", str(REPO / "backend")),
                *("python", "scripts/otlp_expected.py", file.name),
                *("--content-type", export.content_type),
            ],
            cwd=REPO / "backend",
            capture_output=True,
            text=True,
            check=True,
        )
    return json.loads(result.stdout)


def _git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
