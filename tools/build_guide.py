"""Génère docs/Guide-Wanit-Gateway-Flasher.pdf à partir de docs/guide/guide.html (Windows, Microsoft Edge).

Usage : python tools/build_guide.py
"""

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "guide" / "guide.html"
OUTPUT = ROOT / "docs" / "Guide-Wanit-Gateway-Flasher.pdf"
EDGE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def main() -> None:
    edge = next((p for p in EDGE_PATHS if p.exists()), None)
    if edge is None:
        raise SystemExit("Microsoft Edge est introuvable.")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [
                str(edge), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                f"--user-data-dir={profile}", f"--print-to-pdf={OUTPUT}", SOURCE.as_uri(),
            ],
            check=True,
            timeout=120,
        )
    print(f"PDF écrit : {OUTPUT}")


if __name__ == "__main__":
    main()
