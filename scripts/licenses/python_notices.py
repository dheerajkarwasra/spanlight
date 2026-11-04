"""Write the third-party licence notices for the installed Python distributions.

Run it with the interpreter of the environment to document (the image runs it with the
runtime venv, before the project itself is installed, so only dependencies are listed):

    python scripts/licenses/python_notices.py --output THIRD_PARTY_NOTICES.txt
    python scripts/licenses/python_notices.py --check

Each section holds the name, version, licence identifier and the licence files the
distribution ships. A distribution without any licence identifier fails the run (exit 1),
naming the package. Standard library only.
"""

import argparse
import sys
from importlib.metadata import Distribution, distributions
from pathlib import Path

RULE = "=" * 80
LICENCE_FILE_PREFIXES = ("LICEN", "COPYING", "NOTICE")
# The backend image is the only place the output ships; this names what the Python
# distributions cannot list and where those licences live in that image.
HEADER = """\
Third-party Python packages shipped with Spanlight, with their licences.

Not listed below (they are not Python packages):
- The Python interpreter: /usr/local/lib/python3.13/LICENSE.txt
- The Debian packages of the image, PostgreSQL client tools included (postgresql-client-17,
  libpq5): each package's licence is in /usr/share/doc/<package>/copyright
- Native libraries bundled inside wheels: libpq, OpenSSL, Kerberos (krb5), OpenLDAP and
  Cyrus SASL in /opt/venv/lib/python3.13/site-packages/psycopg_binary.libs, and OpenSSL and
  the Rust crates compiled into cryptography. The wheels list them in their SBOMs
  (psycopg_binary-*.dist-info/sboms and cryptography-*.dist-info/sboms in site-packages) but
  do not ship their licence texts.
"""


def licence_id(dist: Distribution) -> str | None:
    meta = dist.metadata
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    licence = (meta.get("License") or "").strip()
    # Some distributions put the whole licence text in License; that is text, not an identifier.
    if licence and licence.upper() != "UNKNOWN" and "\n" not in licence and len(licence) <= 100:
        return licence
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::") and c.split("::")[-1].strip() != "OSI Approved"
    ]
    if classifiers:
        return ", ".join(classifiers)
    if licence and licence.upper() != "UNKNOWN":
        return licence.splitlines()[0].strip()
    return None


def licence_texts(dist: Distribution) -> list[str]:
    texts = []
    for file in dist.files or []:
        in_dist_info = any(part.endswith(".dist-info") for part in file.parts[:-1])
        if in_dist_info and file.name.upper().startswith(LICENCE_FILE_PREFIXES):
            path = Path(str(dist.locate_file(file)))
            if path.is_file():
                texts.append(path.read_text(encoding="utf-8", errors="replace").strip())
    if not texts:
        licence = (dist.metadata.get("License") or "").strip()
        if "\n" in licence:
            texts.append(licence)
    return texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="file to write the notices to")
    mode.add_argument("--check", action="store_true", help="only check; write nothing")
    parser.add_argument(
        "--path", action="append", help="search these directories instead of sys.path"
    )
    args = parser.parse_args(argv)

    seen: dict[tuple[str, str], Distribution] = {}
    for dist in distributions(path=args.path) if args.path else distributions():
        seen.setdefault((dist.metadata["Name"], dist.version), dist)

    sections, missing = [], []
    for (name, version), dist in sorted(
        seen.items(), key=lambda item: (item[0][0].lower(), item[0][1])
    ):
        identifier = licence_id(dist)
        if identifier is None:
            missing.append(f"{name} {version}")
            continue
        texts = licence_texts(dist) or ["(The distribution ships no licence file.)"]
        sections.append(
            f"{RULE}\n{name} {version}\nLicense: {identifier}\n{'-' * 80}\n" + "\n\n".join(texts)
        )

    if missing:
        print("Packages without a licence identifier:", *missing, sep="\n  ", file=sys.stderr)
        return 1
    if args.output:
        args.output.write_text(HEADER + "\n" + "\n\n".join(sections) + "\n", encoding="utf-8")
    print(f"{len(sections)} packages, all with a licence identifier.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
