"""Flash de l'image complète via esptool, avec journal et progression remontés à l'interface."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import serial
from esptool.cmds import attach_flash, detect_chip, reset_chip, run_stub, write_flash
from esptool.logger import TemplateLogger, log
from esptool.util import FatalError

from . import TARGET_CHIP

FLASH_ADDRESS = 0x0
ROM_BAUD = 115200
IMAGE_MAGIC = 0xE9
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_MAGIC = b"\xaa\x50"
CHIP_ID_ESP32C6 = 13

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, int, int], None]


class FlashError(Exception):
    """Erreur présentable telle quelle à l'utilisateur."""


# --- Validation d'un binaire ---------------------------------------------------------

@dataclass
class ImageCheck:
    ok: bool
    message: str
    warning: str | None = None


def check_image(path: Path) -> ImageCheck:
    """Vérifie qu'un fichier est bien une image complète ESP32-C6 à flasher en 0x0."""
    try:
        # Seuls l'en-tête et la table de partitions servent : inutile de charger tout le fichier
        with open(path, "rb") as fh:
            data = fh.read(PARTITION_TABLE_OFFSET + 2)
        size = path.stat().st_size
    except OSError as exc:
        return ImageCheck(False, f"Lecture impossible : {exc.strerror or exc}")

    if "-ota" in path.name.lower():
        return ImageCheck(False, "Image OTA : elle s'envoie à distance, jamais par câble.")
    if len(data) < 16 or data[0] != IMAGE_MAGIC:
        return ImageCheck(False, "Ce fichier n'est pas une image ESP32.")
    if data[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + 2] != PARTITION_MAGIC:
        return ImageCheck(False, "Image incomplète : il faut le fichier « -full.bin » (bootloader et partitions inclus).")
    (chip_id,) = struct.unpack_from("<H", data, 12)
    if chip_id != CHIP_ID_ESP32C6:
        return ImageCheck(False, f"Cette image n'est pas compilée pour l'{TARGET_CHIP}.")

    warning = None
    if "debug" in path.name.lower():
        warning = "Variante debug : expose des informations sensibles sur l'UART."
    return ImageCheck(True, f"Image complète {TARGET_CHIP} · {size / 1_000_000:.1f} Mo".replace(".", ","), warning)


# --- Journal esptool -----------------------------------------------------------------

class GuiLogger(TemplateLogger):
    """Remplace le journal console d'esptool.

    esptool installe un logger en changeant la classe de son instance globale : l'état est donc
    porté par des attributs de classe et non par __init__.
    """

    log_sink: LogFn | None = None
    progress_sink: ProgressFn | None = None
    _buffer = ""

    def print(self, *args, sep=" ", end="\n", **kwargs):
        cls = GuiLogger
        cls._buffer += sep.join(str(a) for a in args) + end
        *lines, cls._buffer = cls._buffer.split("\n")
        for line in lines:
            if line.strip() and cls.log_sink:
                cls.log_sink(line.rstrip())

    def note(self, message):
        self.print(f"Note : {message}")

    def warning(self, message):
        self.print(f"Attention : {message}")

    def error(self, message):
        self.print(message)

    def stage(self, finish=False):
        pass

    def progress_bar(self, cur_iter, total_iters, prefix="", suffix="", bar_length=30):
        if GuiLogger.progress_sink:
            GuiLogger.progress_sink("write", cur_iter, total_iters)

    def set_verbosity(self, verbosity):
        pass


log.set_logger(GuiLogger())


# --- Flash -----------------------------------------------------------------------------

def flash(port: str, image: Path, baud: int, erase_all: bool, on_log: LogFn, on_progress: ProgressFn) -> str:
    """Flashe `image` en 0x0 sur l'ESP32-C6 de `port`. Renvoie l'adresse MAC de l'appareil."""
    GuiLogger.log_sink = on_log
    GuiLogger.progress_sink = on_progress
    try:
        on_progress("connect", 0, 1)
        with detect_chip(port, ROM_BAUD) as esp:
            if esp.CHIP_NAME != TARGET_CHIP:
                raise FlashError(f"L'appareil sur {port} est un {esp.CHIP_NAME}, pas un {TARGET_CHIP}. Rien n'a été écrit.")
            mac = ":".join(f"{b:02x}" for b in esp.read_mac())
            on_log(f"Appareil : {esp.get_chip_description()} · MAC {mac}")
            esp = run_stub(esp)
            if baud != ROM_BAUD:
                esp.change_baud(baud)
            attach_flash(esp)
            if erase_all:
                on_progress("erase", 0, 1)
            write_flash(esp, [(FLASH_ADDRESS, str(image))], erase_all=erase_all, compress=True)
            on_progress("reset", 1, 1)
            reset_chip(esp, "hard-reset")
            return mac
    except FlashError:
        raise
    except Exception as exc:
        on_log(f"Erreur : {type(exc).__name__}: {exc}")
        raise FlashError(explain(exc, port)) from exc
    finally:
        GuiLogger.log_sink = None
        GuiLogger.progress_sink = None


def explain(exc: Exception, port: str) -> str:
    """Traduit les erreurs esptool/pyserial courantes en consignes actionnables."""
    text = str(exc).lower()
    if isinstance(exc, serial.SerialException) or "could not open port" in text:
        if "permission" in text or "access is denied" in text or "accès refusé" in text:
            return f"Le port {port} est occupé. Fermez le moniteur série ou l'autre programme qui l'utilise, puis réessayez."
        if "filenotfound" in text or "cannot find" in text or "no such file" in text:
            return f"Le port {port} n'existe plus. Vérifiez que le module de flash est branché, puis rafraîchissez la liste."
        return f"Communication interrompue sur {port}. Vérifiez le branchement du module de flash et réessayez."
    if isinstance(exc, FatalError):
        if "failed to connect" in text or "no serial data" in text or "timed out" in text:
            return (
                "Le Wanit Gateway ne répond pas. Vérifiez que le connecteur du module de flash est bien enfoncé "
                "sur la carte, dans le bon sens, puis débranchez et rebranchez le module avant de relancer le flash."
            )
        if "wrong chip" in text or "unexpected chip" in text:
            return f"L'appareil connecté n'est pas un {TARGET_CHIP}."
        return f"esptool a signalé une erreur : {exc}"
    if isinstance(exc, StopIteration):
        return "L'appareil a cessé de répondre pendant le flash. Débranchez-le, rebranchez-le et réessayez."
    return f"Erreur inattendue : {exc}"
