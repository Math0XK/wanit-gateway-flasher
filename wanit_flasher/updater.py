"""Auto-mise à jour de l'exécutable depuis les releases GitHub de l'utilitaire."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import FLASHER_ASSET, FLASHER_REPO, __version__
from .github import Asset, GitHubError, ProgressFn, Release, download, expected_sha256, latest_release, parse_version


@dataclass
class Update:
    version: str
    page_url: str
    asset: Asset | None
    release: Release


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def cleanup_previous() -> None:
    """Supprime l'exécutable remplacé lors d'une mise à jour précédente."""
    if is_frozen():
        current_exe().with_suffix(".old").unlink(missing_ok=True)


def check() -> Update | None:
    """Renvoie la mise à jour disponible, ou None si l'utilitaire est à jour."""
    try:
        release = latest_release(FLASHER_REPO)
    except GitHubError as exc:
        if exc.status == 404:  # aucune release publiée
            return None
        raise
    if parse_version(release.tag) <= parse_version(__version__):
        return None
    return Update(release.tag.lstrip("v"), release.page_url, release.assets.get(FLASHER_ASSET), release)


def can_self_update(update: Update) -> bool:
    from .paths import resource

    return (
        is_frozen()
        and sys.platform == "win32"
        and update.asset is not None
        and "release-manifest.json" in update.release.assets
        and "release-manifest.sig" in update.release.assets
        and resource("app_release_public_key.pem").is_file()
    )


def install(update: Update, progress: ProgressFn) -> None:
    """Télécharge la nouvelle version, remplace l'exécutable courant et la lance.

    Windows interdit d'écraser un exe en cours d'exécution, mais autorise à le renommer :
    l'ancien devient `.old` (supprimé au prochain démarrage) et le nouveau prend sa place.
    """
    exe = current_exe()
    new = exe.with_suffix(".new")
    old = exe.with_suffix(".old")
    try:
        sha256 = expected_sha256(update.release, update.asset)
        download(update.asset, new, sha256, progress)
        old.unlink(missing_ok=True)
        exe.rename(old)
        try:
            new.rename(exe)
        except OSError:
            old.rename(exe)
            raise
    except PermissionError as exc:
        new.unlink(missing_ok=True)
        raise GitHubError(
            "Le dossier de l'utilitaire n'est pas modifiable. Téléchargez la nouvelle version depuis GitHub."
        ) from exc

    # Nouvelle instance indépendante : PyInstaller ne doit pas réutiliser le dossier temporaire actuel
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([str(exe)], env=env, close_fds=True, creationflags=subprocess.DETACHED_PROCESS)
