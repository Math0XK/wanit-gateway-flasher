"""Accès aux releases GitHub : liste, téléchargement vérifié et cache hors ligne."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .paths import cache_dir

API = "https://api.github.com"
TIMEOUT = 15
CHUNK = 64 * 1024
SUMS_ASSET = "SHA256SUMS.txt"
MANIFEST_ASSET = "release-manifest.json"
SIGNATURE_ASSET = "release-manifest.sig"

ProgressFn = Callable[[int, int], None]


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None, reset: float | None = None):
        super().__init__(message)
        self.status = status
        self.reset = reset  # heure (epoch) de recharge du quota si la limite de l'API est atteinte

    @property
    def rate_limited(self) -> bool:
        return self.reset is not None


def _rate_limit_reset(exc: urllib.error.HTTPError) -> float | None:
    """Heure de recharge du quota si `exc` est un refus pour limite de l'API, sinon None.

    Sans authentification, GitHub accorde 60 requêtes par heure et par adresse IP publique :
    tous les PC derrière une même box ou un même proxy partagent ce quota.
    """
    if exc.code not in (403, 429) or exc.headers is None:
        return None
    retry_after = exc.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return time.time() + int(retry_after)
    if exc.headers.get("X-RateLimit-Remaining") == "0":
        reset = exc.headers.get("X-RateLimit-Reset", "")
        return float(reset) if reset.isdigit() else time.time() + 3600
    return None


def rate_limit_message(reset: float) -> str:
    return f"Limite de requêtes GitHub atteinte pour cette connexion (réessayez après {time.strftime('%H:%M', time.localtime(reset))})"


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
            sha256=digest.removeprefix("sha256:").lower() if digest.startswith("sha256:") else None,
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


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse toute redirection qui quitterait HTTPS (GitHub redirige les téléchargements vers son CDN)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError(f"redirection non sécurisée refusée : {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirect)


def _open(request: urllib.request.Request):
    if not request.full_url.lower().startswith("https://"):
        raise urllib.error.URLError(f"adresse non sécurisée refusée : {request.full_url}")
    return _opener.open(request, timeout=TIMEOUT)


def _request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": f"WanitGatewayFlasher/{__version__}"},
    )


def _etag_cache() -> Path:
    return cache_dir() / "api-etags.json"


def _load_etags() -> dict:
    try:
        data = json.loads(_etag_cache().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_json(path: str):
    """GET sur l'API. Les requêtes sont conditionnelles (ETag) : une réponse 304 ne consomme pas de quota."""
    etags = _load_etags()
    cached = etags.get(path)
    request = _request(f"{API}{path}")
    if isinstance(cached, dict) and cached.get("etag"):
        request.add_header("If-None-Match", cached["etag"])
    try:
        with _open(request) as resp:
            body = json.load(resp)
            etag = resp.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and isinstance(cached, dict) and "body" in cached:
            return cached["body"]
        reset = _rate_limit_reset(exc)
        message = rate_limit_message(reset) if reset is not None else str(exc)
        raise GitHubError(message, exc.code, reset) from exc
    except Exception as exc:  # réseau, JSON : traité comme « hors ligne »
        raise GitHubError(str(exc), getattr(exc, "code", None)) from exc
    if etag:
        etags[path] = {"etag": etag, "body": body}
        try:
            _etag_cache().write_text(json.dumps(etags), encoding="utf-8")
        except OSError:
            pass
    return body


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


def expected_sha256(release: Release, asset: Asset) -> str:
    """Vérifie le manifeste signé par une clé firmware épinglée et renvoie le hash demandé."""
    manifest_asset = release.assets.get(MANIFEST_ASSET)
    signature_asset = release.assets.get(SIGNATURE_ASSET)
    if not manifest_asset or not signature_asset:
        raise GitHubError("Release non signée : manifeste de firmware absent.")
    try:
        with _open(_request(manifest_asset.url, "application/octet-stream")) as resp:
            raw = resp.read(256 * 1024 + 1)
        with _open(_request(signature_asset.url, "application/octet-stream")) as resp:
            signature = resp.read(4096)
        if len(raw) > 256 * 1024 or not signature:
            raise ValueError("taille de manifeste ou signature invalide")
        document = json.loads(raw)
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if raw != canonical:
            raise ValueError("JSON non canonique")
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from .paths import resource

        repository = release.page_url.split("/releases")[0].removeprefix("https://github.com/")
        key_names = (
            ("app_release_public_key.pem",)
            if repository == "Math0XK/wanit-gateway-flasher"
            else ("firmware_release_public_key.pem", "firmware_release_public_key_backup.pem")
        )
        valid_signature = False
        for key_name in key_names:
            key = serialization.load_pem_public_key(resource(key_name).read_bytes())
            try:
                key.verify(signature, canonical, ec.ECDSA(hashes.SHA256()))
                valid_signature = True
                break
            except Exception:
                continue
        if not valid_signature:
            raise ValueError("signature ECDSA invalide")
        if document.get("repository") != repository:
            raise ValueError("dépôt du manifeste inattendu")
        if document.get("tag") != release.tag:
            raise ValueError("version du manifeste inattendue")
        entries = document.get("assets")
        if not isinstance(entries, list):
            raise ValueError("liste d'assets absente")
        entry = next((item for item in entries if item.get("name") == asset.name), None)
        if not entry or entry.get("size") != asset.size or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            raise ValueError(f"{asset.name} absent ou incohérent dans le manifeste signé")
        return entry["sha256"]
    except Exception as exc:
        raise GitHubError(f"Vérification du manifeste signé impossible : {exc}") from exc


def download(asset: Asset, dest: Path, sha256: str, progress: ProgressFn | None = None) -> Path:
    """Télécharge `asset` vers `dest` et vérifie qu'il correspond à `sha256`. Réutilise `dest` s'il est déjà valide."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise GitHubError(f"Empreinte SHA-256 manquante pour {asset.name} : fichier refusé par sécurité.")
    if dest.exists() and dest.stat().st_size == asset.size and sha256_file(dest) == sha256:
        if progress:
            progress(asset.size, asset.size)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with _open(_request(asset.url, "application/octet-stream")) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(CHUNK):
                done += len(chunk)
                if done > asset.size:
                    raise GitHubError("taille supérieure à celle annoncée")
                out.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(done, asset.size)
        if done != asset.size:
            raise GitHubError(f"fichier incomplet ({done} octets sur {asset.size})")
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise GitHubError(f"Téléchargement de {asset.name} impossible : {exc}") from exc

    if digest.hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise GitHubError(f"Empreinte SHA-256 invalide pour {asset.name} : fichier corrompu ou modifié.")
    tmp.replace(dest)
    return dest


# --- Cache des releases firmware -------------------------------------------------

def _releases_cache(repo: str) -> Path:
    return cache_dir() / f"releases-{repo.replace('/', '_')}.json"


def firmware_releases(repo: str) -> tuple[list[Release], GitHubError | None]:
    """Releases du firmware, et l'erreur rencontrée si elles viennent du cache faute d'accès à l'API."""
    cache = _releases_cache(repo)
    try:
        raw = list_releases(repo)
        cache.write_text(json.dumps(raw), encoding="utf-8")
        error = None
    except GitHubError as exc:
        if not cache.exists():
            raise
        raw = json.loads(cache.read_text(encoding="utf-8"))
        error = exc
    return [Release.from_api(r) for r in raw], error


_FORBIDDEN = set('/:*?"<>|' + chr(92))  # chr(92) : barre oblique inverse


def _safe_name(name: str) -> str:
    """Nom de fichier ou de dossier sûr : pas de séparateur ni de remontée dans l'arborescence."""
    if not name or name in (".", "..") or any(c in _FORBIDDEN or ord(c) < 32 for c in name):
        raise GitHubError(f"Nom refusé : {name!r}")
    return name


def firmware_path(release: Release, asset: Asset) -> Path:
    return cache_dir() / "firmware" / _safe_name(release.tag) / _safe_name(asset.name)


def is_cached(release: Release, asset: Asset) -> bool:
    path = firmware_path(release, asset)
    return path.exists() and path.stat().st_size == asset.size
