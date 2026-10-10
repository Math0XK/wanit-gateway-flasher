"""Accès aux releases GitHub : liste, téléchargement vérifié et cache hors ligne."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .paths import cache_dir

API = "https://api.github.com"
TIMEOUT = 15
CHUNK = 64 * 1024

ProgressFn = Callable[[int, int], None]


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Asset:
    name: str
    url: str
    size: int
    sha256: str | None

    @classmethod
    def from_api(cls, data: dict) -> "Asset":
        digest = data.get("digest") or ""
        return cls(
            name=data["name"],
            url=data["browser_download_url"],
            size=data["size"],
            sha256=digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
        )


@dataclass
class Release:
    tag: str
    published: str
    prerelease: bool
    page_url: str
    assets: dict[str, Asset] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict) -> "Release":
        return cls(
            tag=data["tag_name"],
            published=(data.get("published_at") or "")[:10],
            prerelease=bool(data.get("prerelease")),
            page_url=data["html_url"],
            assets={a["name"]: Asset.from_api(a) for a in data.get("assets", [])},
        )


def parse_version(tag: str) -> tuple[int, ...]:
    """'v2.1.0' -> (2, 1, 0). Les suffixes (-rc1…) sont ignorés."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", tag.strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else ()


def _request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": f"WanitGatewayFlasher/{__version__}"},
    )


def get_json(path: str):
    try:
        with urllib.request.urlopen(_request(f"{API}{path}"), timeout=TIMEOUT) as resp:
            return json.load(resp)
    except Exception as exc:  # réseau, HTTP, JSON : tout est traité comme « hors ligne »
        raise GitHubError(str(exc), getattr(exc, "code", None)) from exc


def list_releases(repo: str) -> list[dict]:
    """Releases publiées (hors brouillons), de la plus récente à la plus ancienne."""
    data = get_json(f"/repos/{repo}/releases?per_page=100")
    return [r for r in data if not r.get("draft")]


def latest_release(repo: str) -> Release:
    return Release.from_api(get_json(f"/repos/{repo}/releases/latest"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(asset: Asset, dest: Path, progress: ProgressFn | None = None) -> Path:
    """Télécharge `asset` vers `dest` en vérifiant son SHA-256. Réutilise `dest` s'il est déjà valide."""
    if dest.exists() and dest.stat().st_size == asset.size:
        if asset.sha256 is None or sha256_file(dest) == asset.sha256:
            if progress:
                progress(asset.size, asset.size)
            return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(_request(asset.url, "application/octet-stream"), timeout=TIMEOUT) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, asset.size)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise GitHubError(f"Téléchargement de {asset.name} impossible : {exc}") from exc

    if asset.sha256 and digest.hexdigest() != asset.sha256:
        tmp.unlink(missing_ok=True)
        raise GitHubError(f"Empreinte SHA-256 invalide pour {asset.name} : fichier corrompu ou modifié.")
    tmp.replace(dest)
    return dest


# --- Cache des releases firmware -------------------------------------------------

def _releases_cache(repo: str) -> Path:
    return cache_dir() / f"releases-{repo.replace('/', '_')}.json"


def firmware_releases(repo: str) -> tuple[list[Release], bool]:
    """Releases du firmware, et True si elles viennent du cache faute de réseau."""
    cache = _releases_cache(repo)
    try:
        raw = list_releases(repo)
        cache.write_text(json.dumps(raw), encoding="utf-8")
        offline = False
    except GitHubError:
        if not cache.exists():
            raise
        raw = json.loads(cache.read_text(encoding="utf-8"))
        offline = True
    return [Release.from_api(r) for r in raw], offline


def firmware_path(release: Release, asset: Asset) -> Path:
    return cache_dir() / "firmware" / release.tag / asset.name


def is_cached(release: Release, asset: Asset) -> bool:
    path = firmware_path(release, asset)
    return path.exists() and path.stat().st_size == asset.size
