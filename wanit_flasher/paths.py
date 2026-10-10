"""Emplacements des ressources embarquées et du cache local."""

import os
import sys
from pathlib import Path


def resource(name: str) -> Path:
    """Chemin d'un fichier de `wanit_flasher/assets`, en développement comme dans l'exe PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "wanit_flasher" / "assets" / name


def cache_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = root / "WanitGatewayFlasher"
    path.mkdir(parents=True, exist_ok=True)
    return path
