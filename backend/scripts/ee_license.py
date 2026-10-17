"""Generate a license signing keypair, or mint an enterprise license key. Maintainer only.

Run from `backend/`:

    uv run python scripts/ee_license.py keygen --out ~/secure/spanlight-ee-2026-10.pem
    uv run python scripts/ee_license.py mint --private-key ~/secure/spanlight-ee-2026-10.pem \\
        --kid 2026-10 --licensee "Acme Inc" --expires 2027-10-15 --all-features

`keygen` writes the private key (mode 0600) and prints the public key's hex for
`app.ee.license.PUBLIC_KEYS`. The private key never goes into the repository: whoever holds it can
mint any license. `mint` prints a key for `EE_LICENSE_KEY` and verifies it against the public key
in the code before printing it, so a key from the wrong private key or `kid` never leaves.
"""

import argparse
import base64
import json
import sys
import uuid
from datetime import UTC, datetime, time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.ee.features import EEFeature
from app.ee.license import FORMAT_VERSION, KEY_PREFIX, parse_license


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def keygen(out: Path) -> None:
    if out.exists():
        sys.exit(f"{out} exists; refusing to overwrite a signing key")
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    out.touch(mode=0o600)
    out.write_bytes(pem)
    public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print(public.hex())


def mint(args: argparse.Namespace) -> None:
    private_key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        sys.exit("the private key is not an Ed25519 key")
    known = {feature.value for feature in EEFeature}
    features = sorted(known) if args.all_features else sorted(set(args.feature))
    if unknown := set(features) - known:
        sys.exit(f"unknown features: {', '.join(sorted(unknown))}")
    expires = datetime.combine(datetime.fromisoformat(args.expires).date(), time(), tzinfo=UTC)
    payload = {
        "v": FORMAT_VERSION,
        "kid": args.kid,
        "license_id": str(uuid.uuid4()),
        "licensee": args.licensee,
        "issued_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "expires_at": expires.isoformat(),
        "features": features,
    }
    segment = _b64(json.dumps(payload, separators=(",", ":")).encode())
    key = f"{KEY_PREFIX}{segment}.{_b64(private_key.sign(segment.encode('ascii')))}"
    parse_license(key)  # raises unless the code's public key for `kid` verifies it
    print(key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    keygen_parser = commands.add_parser("keygen", help="write a new private key")
    keygen_parser.add_argument("--out", type=Path, required=True)
    mint_parser = commands.add_parser("mint", help="print a license key")
    mint_parser.add_argument("--private-key", type=Path, required=True)
    mint_parser.add_argument("--kid", required=True)
    mint_parser.add_argument("--licensee", required=True)
    mint_parser.add_argument("--expires", required=True, help="YYYY-MM-DD, midnight UTC")
    granted = mint_parser.add_mutually_exclusive_group()
    granted.add_argument("--feature", action="append", default=[], help="repeat per feature")
    granted.add_argument("--all-features", action="store_true", help="every feature built today")
    args = parser.parse_args()
    if args.command == "keygen":
        keygen(args.out)
    else:
        mint(args)


if __name__ == "__main__":
    main()
