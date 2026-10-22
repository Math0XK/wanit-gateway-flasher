"""Create a detached ECDSA-SHA256 manifest for a GitHub release, offline.

Keep the PEM private key on a signing machine. Never add it to this repository or CI secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from getpass import getpass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument("--tag", required=True, help="Release tag, for example v1.2.3")
    parser.add_argument("--private-key", required=True, type=Path, help="Offline PEM private key")
    parser.add_argument("--asset", required=True, action="append", type=Path, help="Release asset; repeat per file")
    parser.add_argument("--output", type=Path, default=Path("."))
    args = parser.parse_args()

    pem = args.private_key.read_bytes()
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except TypeError:
        key = serialization.load_pem_private_key(pem, password=getpass("Private key password: ").encode())
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        parser.error("the private key must be ECDSA P-256")
    assets = []
    for path in args.asset:
        raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append({"name": path.name, "size": path.stat().st_size, "sha256": raw_hash})
    manifest = {"assets": sorted(assets, key=lambda item: item["name"]), "repository": args.repository, "tag": args.tag}
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "release-manifest.json").write_bytes(canonical)
    (args.output / "release-manifest.sig").write_bytes(key.sign(canonical, ec.ECDSA(hashes.SHA256())))


if __name__ == "__main__":
    main()
