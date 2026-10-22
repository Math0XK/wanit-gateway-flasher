"""Create the canonical JSON manifest signed for a GitHub release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--asset", action="append", type=Path, default=[])
    parser.add_argument("--assets-dir", type=Path, help="Include each regular file directly inside this directory")
    parser.add_argument("--output", type=Path, default=Path("."))
    args = parser.parse_args()
    assets = []
    paths = list(args.asset)
    if args.assets_dir:
        paths.extend(path for path in args.assets_dir.iterdir() if path.is_file())
    if not paths:
        parser.error("provide at least one --asset or --assets-dir")
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append({"name": path.name, "size": path.stat().st_size, "sha256": digest})
    manifest = {"assets": sorted(assets, key=lambda item: item["name"]), "repository": args.repository, "tag": args.tag}
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "release-manifest.json").write_bytes(canonical)


if __name__ == "__main__":
    main()
