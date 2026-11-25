"""Flash de l'image complète via esptool, avec journal et progression remontés à l'interface."""

from __future__ import annotations

import struct
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import serial
from esptool.cmds import attach_flash, detect_chip, reset_chip, run_stub, write_flash
from esptool.logger import TemplateLogger, log
from esptool.util import FatalError

from . import TARGET_CHIP, partitions

FLASH_ADDRESS = 0x0
SECTOR_SIZE = 0x1000
ROM_BAUD = 115200
IMAGE_MAGIC = 0xE9
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_MAGIC = b"\xaa\x50"
PARTITION_ENTRY_SIZE = 32
PARTITION_TABLE_END = b"\xeb\xeb"
PARTITION_TABLE_MAX_ENTRIES = 96  # Jusqu'à 95 partitions, plus le marqueur de fin
MAX_IMAGE_SIZE = 16 * 1024 * 1024
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
    authenticated: bool = False


def _locally_signed(path: Path, size: int) -> bool:
    """True only when an adjacent firmware manifest authenticates this exact file."""
    manifest_path = path.with_name("release-manifest.json")
    signature_path = path.with_name("release-manifest.sig")
    try:
        with manifest_path.open("rb") as fh:
            raw = fh.read(256 * 1024 + 1)
        with signature_path.open("rb") as fh:
            signature = fh.read(4097)
        if len(raw) > 256 * 1024 or len(signature) > 4096:
            return False
        manifest = json.loads(raw)
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if raw != canonical or manifest.get("repository") != "Math0XK/wanit-gateway-releases":
            return False
        entry = next((item for item in manifest.get("assets", []) if item.get("name") == path.name), None)
        if not entry or entry.get("size") != size or entry.get("sha256") != _file_sha256(path):
            return False
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from .paths import resource

        for name in ("firmware_release_public_key.pem", "firmware_release_public_key_backup.pem"):
            key = serialization.load_pem_public_key(resource(name).read_bytes())
            try:
                key.verify(signature, canonical, ec.ECDSA(hashes.SHA256()))
                return True
            except Exception:
                pass
    except Exception:
        return False
    return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_image(path: Path) -> ImageCheck:
    """Vérifie qu'un fichier est bien une image complète ESP32-C6 à flasher en 0x0."""
    try:
        # Seuls l'en-tête et la table de partitions servent : inutile de charger tout le fichier
        with open(path, "rb") as fh:
            data = fh.read(PARTITION_TABLE_OFFSET + PARTITION_ENTRY_SIZE * PARTITION_TABLE_MAX_ENTRIES)
        size = path.stat().st_size
    except OSError as exc:
        return ImageCheck(False, f"Lecture impossible : {exc.strerror or exc}")

    if "-ota" in path.name.lower():
        return ImageCheck(False, "Image OTA : elle s'envoie à distance, jamais par câble.")
    if len(data) < 16 or data[0] != IMAGE_MAGIC:
        return ImageCheck(False, "Ce fichier n'est pas une image ESP32.")
    if size < PARTITION_TABLE_OFFSET + PARTITION_ENTRY_SIZE or size > MAX_IMAGE_SIZE:
        return ImageCheck(False, "Taille d'image invalide ou supérieure à 16 Mo.")
    if len(data) < 24 or data[1] == 0 or data[1] > 16:
        return ImageCheck(False, "En-tête ESP invalide : nombre de segments incorrect.")
    if data[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + 2] != PARTITION_MAGIC:
        return ImageCheck(False, "Image incomplète : il faut le fichier « -full.bin » (bootloader et partitions inclus).")
    (chip_id,) = struct.unpack_from("<H", data, 12)
    if chip_id != CHIP_ID_ESP32C6:
        return ImageCheck(False, f"Cette image n'est pas compilée pour l'{TARGET_CHIP}.")

    # Une table ESP-IDF est constituée d'entrées de 32 octets, terminée par 0xEBEB.
    # Vérifie sa terminaison, les types, les tailles, les offsets et les recouvrements.
    entries: list[tuple[int, int]] = []
    terminated = False
    table = data[PARTITION_TABLE_OFFSET:]
    for offset in range(0, len(table) - PARTITION_ENTRY_SIZE + 1, PARTITION_ENTRY_SIZE):
        entry = table[offset:offset + PARTITION_ENTRY_SIZE]
        if entry[:2] == PARTITION_TABLE_END:
            terminated = True
            break
        if entry[:2] != PARTITION_MAGIC:
            return ImageCheck(False, "Table de partitions invalide : entrée ou marqueur de fin absent.")
        partition_offset, partition_size = struct.unpack_from("<II", entry, 4)
        if partition_size == 0 or partition_offset < 0x9000 or partition_offset + partition_size > 0x1000000:
            return ImageCheck(False, "Table de partitions invalide : adresse ou taille hors limites.")
        if any(partition_offset < end and start < partition_offset + partition_size for start, end in entries):
            return ImageCheck(False, "Table de partitions invalide : partitions qui se chevauchent.")
        entries.append((partition_offset, partition_offset + partition_size))
    if not terminated or not entries:
        return ImageCheck(False, "Table de partitions invalide : marqueur de fin absent ou aucune partition.")

    warning = None
    if "debug" in path.name.lower():
        warning = "Variante debug : expose des informations sensibles sur l'UART."
    return ImageCheck(
        True,
        f"Image complète {TARGET_CHIP} · {size / 1_000_000:.1f} Mo".replace(".", ","),
        warning,
        _locally_signed(path, size),
    )


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

@dataclass
class FlashResult:
    mac: str
    report: partitions.DeviceReport


def _open(port: str, baud: int, on_log: LogFn):
    """Connecte l'ESP32-C6, charge le stub et attache la flash. À utiliser dans un `with`."""
    esp = detect_chip(port, ROM_BAUD)
    try:
        if esp.CHIP_NAME != TARGET_CHIP:
            raise FlashError(f"L'appareil sur {port} est un {esp.CHIP_NAME}, pas un {TARGET_CHIP}. Rien n'a été écrit.")
        mac = ":".join(f"{b:02x}" for b in esp.read_mac())
        on_log(f"Appareil : {esp.get_chip_description()} · MAC {mac}")
        stub = run_stub(esp)
        if baud != ROM_BAUD:
            stub.change_baud(baud)
        attach_flash(stub)
        return esp, stub, mac
    except BaseException:
        esp._port.close()
        raise


def _log_report(report: partitions.DeviceReport, on_log: LogFn) -> None:
    for line in report.lines():
        on_log(f"  {line}")


def _write_plan(image: Path, erase_all: bool, on_log: LogFn) -> list[tuple[int, bytes]]:
    """Découpe l'image en blocs à écrire, en sautant les partitions NVS qu'elle laisse vides.

    L'image complète contient des 0xFF sur toute la NVS : l'écrire telle quelle effaçait le
    Wi-Fi, l'UID, le nom et le token de l'appareil. Une NVS vide dans l'image est donc sautée
    et celle de l'appareil conservée. Une NVS non vide dans l'image est écrite (réglages
    voulus par qui a construit l'image). Avec l'effacement complet, tout est écrit.
    """
    data = image.read_bytes()
    if erase_all:
        return [(FLASH_ADDRESS, data)]
    skipped: list[tuple[int, int]] = []
    for part in partitions.parse_table(data[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + partitions.PARTITION_TABLE_SIZE]):
        start, end = part.offset, min(part.offset + part.size, len(data))
        if not part.is_nvs or start >= len(data):
            continue
        if start % SECTOR_SIZE or (end < len(data) and end % SECTOR_SIZE):
            continue  # esptool efface par secteurs entiers : un bord non aligné toucherait la NVS
        if data[start:end].count(0xFF) != end - start:
            on_log(f"NVS {part.label} @0x{start:X} : l'image fournit un contenu, il remplace celui de l'appareil.")
            continue
        skipped.append((start, end))
        on_log(f"NVS {part.label} @0x{start:X} conservée (Wi-Fi, UID, nom, token).")
    plan, pos = [], 0
    for start, end in sorted(skipped):
        if start > pos:
            plan.append((pos, data[pos:start]))
        pos = end
    if pos < len(data):
        plan.append((pos, data[pos:]))
    return plan


def _neutralize_stale_slots(esp, image: Path, on_log: LogFn) -> None:
    """Empêche tout ancien firmware resté hors de l'image de redémarrer.

    L'image complète réécrit bootloader, table, otadata et app0, mais pas app1 : un firmware
    installé auparavant par OTA y reste intact. Si l'otadata le désigne encore, ou si le
    firmware neuf déclenche un rollback, l'appareil repart sur cette ancienne version.
    On efface donc le premier secteur de chaque slot applicatif non réécrit, ce qui le rend
    non amorçable.
    """
    image_end = image.stat().st_size
    for part in partitions.read_table(esp.read_flash):
        if part.is_app and part.offset >= image_end:
            app = partitions.read_app(esp.read_flash, part.offset, part.size)
            if app.present:
                esp.erase_region(part.offset, SECTOR_SIZE)
                on_log(f"Slot {part.label} @0x{part.offset:X} : ancien firmware {app.label} neutralisé.")


def _select_boot_slot(esp, written: partitions.Partition, on_log: LogFn) -> None:
    """Réécrit l'otadata pour qu'elle désigne explicitement le slot qui vient d'être flashé.

    On ne se fie pas à l'otadata embarquée dans l'image : un fichier local peut ne pas en
    contenir, ou en contenir une qui désigne un autre slot.
    """
    otadata = next((p for p in partitions.read_table(esp.read_flash) if p.is_otadata), None)
    if otadata is None:
        return
    if written.ota_index is None:
        # Slot factory : une otadata vide fait démarrer factory.
        esp.erase_region(otadata.offset, otadata.size)
        on_log(f"otadata @0x{otadata.offset:X} effacée : démarrage sur {written.label}.")
        return
    data = partitions.otadata_selecting(written.ota_index, otadata.size)
    write_flash(esp, [(otadata.offset, data)], compress=True, no_progress=True)
    on_log(f"otadata @0x{otadata.offset:X} : démarrage fixé sur {written.label} (état VALID).")


def flash(port: str, image: Path, baud: int, erase_all: bool, on_log: LogFn, on_progress: ProgressFn) -> FlashResult:
    """Flashe `image` en 0x0 sur l'ESP32-C6 de `port`, puis vérifie quel firmware démarrera."""
    GuiLogger.log_sink = on_log
    GuiLogger.progress_sink = on_progress
    try:
        written = _image_app_slot(image)
        expected = partitions.read_app(partitions.file_reader(image), written.offset, written.size)
        on_progress("connect", 0, 1)
        esp, stub, mac = _open(port, baud, on_log)
        with esp:
            if erase_all:
                on_progress("erase", 0, 1)
            write_flash(stub, _write_plan(image, erase_all, on_log), erase_all=erase_all, compress=True)
            on_progress("verify", 0, 1)
            _neutralize_stale_slots(stub, image, on_log)
            _select_boot_slot(stub, written, on_log)
            report = partitions.inspect(stub.read_flash)
            on_log("Firmware installé par slot :")
            _log_report(report, on_log)
            boot = report.boot
            if boot is None or boot.partition.offset != written.offset or not boot.selected or boot.app.label != expected.label:
                running = f"{boot.app.label} ({boot.partition.label})" if boot else "aucun firmware"
                raise FlashError(
                    f"L'écriture a réussi mais l'appareil démarrerait sur « {running} » au lieu de "
                    f"« {expected.label} ({written.label}) ». Relancez le flash avec « Effacer toute la flash avant d'écrire »."
                )
            on_progress("reset", 1, 1)
            reset_chip(stub, "hard-reset")
            return FlashResult(mac, report)
    except FlashError:
        raise
    except Exception as exc:
        on_log(f"Erreur : {type(exc).__name__}: {exc}")
        raise FlashError(explain(exc, port)) from exc
    finally:
        GuiLogger.log_sink = None
        GuiLogger.progress_sink = None


def read_versions(port: str, baud: int, on_log: LogFn, on_progress: ProgressFn) -> FlashResult:
    """Lit, sans rien écrire, la version présente dans chaque slot et celle qui démarre."""
    GuiLogger.log_sink = on_log
    GuiLogger.progress_sink = None  # la lecture n'a pas de barre d'écriture
    try:
        on_progress("connect", 0, 1)
        esp, stub, mac = _open(port, baud, on_log)
        with esp:
            on_progress("read", 0, 1)
            report = partitions.inspect(stub.read_flash)
            on_log("Firmware installé par slot :")
            _log_report(report, on_log)
            reset_chip(stub, "hard-reset")
            return FlashResult(mac, report)
    except FlashError:
        raise
    except Exception as exc:
        on_log(f"Erreur : {type(exc).__name__}: {exc}")
        raise FlashError(explain(exc, port)) from exc
    finally:
        GuiLogger.log_sink = None


def _image_app_slot(image: Path) -> partitions.Partition:
    """Slot applicatif que l'image écrit (le premier qu'elle couvre)."""
    read = partitions.file_reader(image)
    size = image.stat().st_size
    apps = [p for p in partitions.read_table(read) if p.is_app and p.offset < size]
    if not apps:
        raise FlashError("L'image ne contient aucun firmware applicatif.")
    return min(apps, key=lambda p: p.offset)


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
