"""Construit dist/WanitGatewayFlasher.exe avec PyInstaller.

Usage : python tools/build.py
"""

import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wanit_flasher import APP_NAME, FLASHER_ASSET, __version__  # noqa: E402

BUILD = ROOT / "build"


def version_file() -> Path:
    """Ressource de version Windows (Propriétés > Détails de l'exe)."""
    parts = [int(p) for p in __version__.split(".")] + [0] * 4
    numbers = tuple(parts[:4])
    BUILD.mkdir(exist_ok=True)
    path = BUILD / "version_info.txt"
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'Wanit'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('OriginalFilename', '{FLASHER_ASSET}'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x040C, 1200])]),
  ],
)
""",
        encoding="utf-8",
    )
    return path


def main() -> None:
    sep = ";" if sys.platform == "win32" else ":"
    PyInstaller.__main__.run([
        str(ROOT / "wanit_flasher" / "__main__.py"),
        "--name", Path(FLASHER_ASSET).stem,
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", str(ROOT / "wanit_flasher" / "assets" / "icon.ico"),
        "--version-file", str(version_file()),
        "--add-data", f"{ROOT / 'wanit_flasher' / 'assets'}{sep}wanit_flasher/assets",
        # Thèmes CustomTkinter et stubs flasheurs d'esptool (fichiers JSON chargés à l'exécution)
        "--collect-data", "customtkinter",
        "--collect-data", "esptool",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
    ])


if __name__ == "__main__":
    main()
