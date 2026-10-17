"""Record one OTLP compatibility fixture (spec sections 8.1 and 8.2).

    uv run python record.py --library otel-genai-python --version 2.4b0
    uv run python record.py --library otel-genai-python --version latest --check
    uv run python record.py --from-capture DIR --library vercel-ai-genai --version 7.0.142

A Python library is recorded from its recipe `python/recipes/<library>.py` (hyphens become
underscores): the recipe's `PACKAGE` at `--version` (`latest` leaves it unpinned) plus its pinned
`PACKAGES` and the OTel SDK and exporter are installed into a `uv` virtual environment in a
temporary directory, never into any project venv, and each of `SCENARIOS` runs as a subprocess
against the fake provider with the capture server as its OTLP endpoint. `--from-capture DIR`
skips that part and reads exports another recorder captured (the Node recipe): one
`DIR/<scenario>/NNNN.pb` or `.json` per scenario and `DIR/recorder.json` with `runtime`,
`otel_sdk` and optional `loses`.

`preview` is the export decoded to OTLP/JSON for review; `expected` is what
`backend/scripts/otlp_expected.py` prints for the bytes. The fixture is written to
`<out>/<library>/<version>.json` (`--out` defaults to `sdks/fixtures/otlp`). `--check` writes it
too, then diffs `expected` field by field against the committed fixture under `--fixtures-root`,
one `scenarios[...]` line per difference, and exits 1 on any.
"""

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

from _document import PROTOBUF, Export, Recorder, build_document, diff_expected
from capture import serve as serve_capture
from fake_provider import serve as serve_provider

HERE = Path(__file__).resolve().parent
FIXTURES_ROOT = HERE.parent
OTEL_SDK_VERSION = "1.45.1"
OTEL_PACKAGES = {
    "opentelemetry-sdk": OTEL_SDK_VERSION,
    "opentelemetry-exporter-otlp-proto-http": OTEL_SDK_VERSION,
}
SCENARIO_TIMEOUT_SECONDS = 120
_LIBRARY = re.compile(r"[a-z0-9-]+")
_RUN_SCENARIO = "import importlib, sys; importlib.import_module(sys.argv[1]).run(*sys.argv[2:])"

sys.path.insert(0, str(HERE / "python"))


def load_recipe(library: str) -> ModuleType:
    if not _LIBRARY.fullmatch(library):
        sys.exit(f"invalid library name {library!r}")
    try:
        return importlib.import_module(f"recipes.{library.replace('-', '_')}")
    except ModuleNotFoundError as exc:
        sys.exit(f"no recipe for {library!r}: {exc}")


def make_venv(tmp: Path, recipe: ModuleType, version: str) -> tuple[Path, str]:
    """Create the throwaway venv, install the pins; the resolved library version comes back."""
    venv = tmp / "venv"
    python = venv / "bin" / "python"
    pins = {recipe.PACKAGE: version, **recipe.PACKAGES, **OTEL_PACKAGES}
    specs = [name if pin == "latest" else f"{name}=={pin}" for name, pin in pins.items()]
    subprocess.run(["uv", "venv", "--quiet", "--python", "3.13", str(venv)], check=True)
    subprocess.run(["uv", "pip", "install", "--quiet", "--python", str(python), *specs], check=True)
    resolved = subprocess.run(
        [python, "-c", "import importlib.metadata as m; print(m.version(input()))"],
        input=recipe.PACKAGE,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return python, resolved


def runtime_of(python: Path) -> str:
    version = subprocess.run([python, "--version"], capture_output=True, text=True, check=True)
    return version.stdout.strip().lower()


def run_scenarios(python: Path, recipe: ModuleType) -> list[Export]:
    """Run every recipe scenario as a subprocess; exactly one export per scenario."""
    provider = serve_provider()
    capture = serve_capture()
    exports: list[Export] = []
    env = {
        **os.environ,
        "PYTHONPATH": str(HERE / "python"),
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{capture.url}/v1/otlp/traces",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_SERVICE_NAME": "spanlight-fixture",
        "OPENAI_BASE_URL": f"{provider.url}/v1",
        "ANTHROPIC_BASE_URL": provider.url,
        "OPENAI_API_KEY": "sk-fixture",
        "ANTHROPIC_API_KEY": "sk-fixture",
    }
    try:
        for scenario in recipe.SCENARIOS:
            before = len(capture.exports)
            subprocess.run(
                [python, "-c", _RUN_SCENARIO, recipe.__name__, scenario, provider.url],
                env={**env, "X_FIXTURE_SCENARIO": scenario},
                check=True,
                timeout=SCENARIO_TIMEOUT_SECONDS,
            )
            received = capture.exports[before:]
            if len(received) != 1:
                sys.exit(f"{scenario}: expected one OTLP export, the capture got {len(received)}")
            content_type, body = received[0]
            exports.append(Export(scenario, content_type.split(";")[0].strip(), body))
    finally:
        provider.close()
        capture.close()
    return exports


def load_capture(directory: Path) -> tuple[Recorder, list[Export]]:
    """Exports another recorder captured: `<dir>/<scenario>/NNNN.pb|json` + `recorder.json`."""
    meta = json.loads((directory / "recorder.json").read_text())
    recorder = Recorder(meta["runtime"], meta["otel_sdk"], meta.get("loses", {}))
    exports: list[Export] = []
    for scenario_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        files = sorted(p for p in scenario_dir.iterdir() if p.suffix in (".pb", ".json"))
        if len(files) != 1:
            sys.exit(f"{scenario_dir}: expected one export file, found {len(files)}")
        content_type = PROTOBUF if files[0].suffix == ".pb" else "application/json"
        exports.append(Export(scenario_dir.name, content_type, files[0].read_bytes()))
    return recorder, exports


def committed_fixture(root: Path, library: str, version: str) -> Path:
    """The committed file `--check` diffs against; `latest` picks the highest version on disk."""
    if version != "latest":
        return root / library / f"{version}.json"
    files = list((root / library).glob("*.json"))
    if not files:
        sys.exit(f"no committed fixture under {root / library}")
    return max(
        files,
        key=lambda p: [
            (t.isdigit(), int(t) if t.isdigit() else t) for t in re.split(r"(\d+)", p.stem)
        ],
    )


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--version", required=True, help="a version or `latest`")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path, default=FIXTURES_ROOT)
    parser.add_argument("--fixtures-root", type=Path, default=FIXTURES_ROOT)
    parser.add_argument("--from-capture", type=Path, default=None, metavar="DIR")
    return parser.parse_args()


def main() -> int:
    os.environ["TMPDIR"] = tempfile.tempdir = "/tmp"  # noqa: S108 - no per-user path in fixtures
    args = _args()
    version = args.version
    if args.from_capture is not None:
        recorder, exports = load_capture(args.from_capture)
    else:
        recipe = load_recipe(args.library)
        with tempfile.TemporaryDirectory(prefix="spanlight-record-") as tmp:
            python, version = make_venv(Path(tmp), recipe, version)
            recorder = Recorder(runtime_of(python), OTEL_SDK_VERSION, recipe.LOSES)
            exports = run_scenarios(python, recipe)
    document = build_document(args.library, version, recorder, exports)
    target = args.out / args.library / f"{version}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    print(target)
    if not args.check:
        return 0
    committed = committed_fixture(args.fixtures_root, args.library, args.version)
    lines = diff_expected(json.loads(committed.read_text()), document)
    print("\n".join(lines) if lines else f"expected unchanged against {committed}")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
