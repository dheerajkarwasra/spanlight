"""Packaging guarantees: typed, lightweight import, stable public API."""

from __future__ import annotations

import subprocess
import sys
from importlib import resources

import spanlight


def test_package_is_marked_typed():
    assert resources.files("spanlight").joinpath("py.typed").is_file()


def test_import_does_not_load_optional_provider_sdks():
    code = (
        "import sys, spanlight; print(any(name in sys.modules for name in ('openai', 'anthropic')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_public_api():
    assert spanlight.__version__
    for name in ("init", "observe", "span", "update_trace", "wrap_openai", "wrap_anthropic"):
        assert callable(getattr(spanlight, name))


def test_package_exports_batch_helper():
    assert "record_openai_batch_output" in spanlight.__all__
    assert callable(spanlight.record_openai_batch_output)
