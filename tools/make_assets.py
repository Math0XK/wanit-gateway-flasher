"""Génère les images de l'interface et l'icône Windows à partir du logo source.

Usage : python tools/make_assets.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "brand" / "wanit-logo.jpg"
OUT = ROOT / "wanit_flasher" / "assets"

BLUE = (32, 155, 215)
NAVY = (38, 56, 80)
WHITE = (255, 255, 255)

# Zones du logo source (2000 x 2000), avec une petite marge
MARK_BOX = (744, 518, 1254, 1126)
TEXT_BOX = (246, 1244, 1754, 1482)


def unmultiply(img: Image.Image, ink: tuple[int, int, int], out_color: tuple[int, int, int]) -> Image.Image:
    """Transforme un tracé de couleur `ink` sur fond blanc en tracé `out_color` sur fond transparent."""
    gray = img.convert("RGB")
    # Canal le plus contrasté par rapport au blanc pour estimer l'opacité
    channel = min(range(3), key=lambda i: ink[i])
    span = 255 - ink[channel]
    alpha = gray.getchannel(channel).point(lambda v: max(0, min(255, round((255 - v) * 255 / span))))
    result = Image.new("RGBA", gray.size, out_color + (0,))
    result.putalpha(alpha)
    return result


def fit_height(img: Image.Image, height: int) -> Image.Image:
    width = round(img.width * height / img.height)
    return img.resize((width, height), Image.LANCZOS)


def make_icon(mark: Image.Image) -> Image.Image:
    size = 512
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(icon).rounded_rectangle((0, 0, size - 1, size - 1), radius=110, fill=WHITE + (255,))
    inner = fit_height(mark, 380)
    icon.alpha_composite(inner, ((size - inner.width) // 2, (size - inner.height) // 2))
    return icon


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logo = Image.open(SRC)
    mark = unmultiply(logo.crop(MARK_BOX), BLUE, BLUE)
    wordmark = unmultiply(logo.crop(TEXT_BOX), NAVY, WHITE)

    # Rendu net sur écrans haute densité : CTkImage redimensionne à la taille demandée
    fit_height(mark, 128).save(OUT / "mark.png")
    fit_height(wordmark, 64).save(OUT / "wordmark_white.png")

    icon = make_icon(mark)
    icon.save(OUT / "icon.png")
    icon.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Assets écrits dans {OUT}")


if __name__ == "__main__":
    main()
