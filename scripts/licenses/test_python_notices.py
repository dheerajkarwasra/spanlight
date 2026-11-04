"""Tests for python_notices.py on tiny installed-distribution fixtures (stdlib + pytest only)."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("python_notices.py")


def _dist(
    site: Path, name: str, version: str, headers: str, licence_text: str | None = None
) -> None:
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{headers}"
    )
    record = [f"{info.name}/METADATA,,"]
    if licence_text is not None:
        (info / "licenses").mkdir()
        (info / "licenses" / "LICENSE").write_text(licence_text)
        record.append(f"{info.name}/licenses/LICENSE,,")
    (info / "RECORD").write_text("\n".join(record) + "\n")


def _run(site: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(site), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_writes_one_section_per_package(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _dist(site, "alpha", "1.0", "License-Expression: MIT\n", "MIT licence text of alpha\n")
    _dist(site, "beta", "2.1", "Classifier: License :: OSI Approved :: BSD License\n")
    out = tmp_path / "NOTICES.txt"

    result = _run(site, "--output", str(out))

    assert result.returncode == 0, result.stderr
    text = out.read_text()
    assert "alpha 1.0\nLicense: MIT\n" in text
    assert "MIT licence text of alpha" in text
    assert "beta 2.1\nLicense: BSD License\n" in text
    assert text.index("alpha 1.0") < text.index("beta 2.1")


def test_package_without_licence_fails_and_is_named(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _dist(site, "alpha", "1.0", "License: Apache-2.0\n")
    _dist(site, "nolicence", "0.3", "Summary: no licence metadata here\n")

    result = _run(site, "--check")

    assert result.returncode != 0
    assert "nolicence 0.3" in result.stderr
    assert "alpha" not in result.stderr
