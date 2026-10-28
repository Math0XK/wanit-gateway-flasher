import sys
from pathlib import Path

# Lancé par son chemin (python wanit_flasher/__main__.py), Python ne voit pas le dossier du dépôt :
# on l'ajoute pour que `import wanit_flasher` fonctionne aussi dans ce cas.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wanit_flasher.app import main  # noqa: E402

if __name__ == "__main__":
    main()
