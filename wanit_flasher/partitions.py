"""Lecture de l'état réel de la flash : table de partitions, otadata et version de chaque slot.

Ces fonctions ne parlent pas au port série : elles reçoivent une fonction `read(offset, length)`,
ce qui permet de les appliquer aussi bien à l'appareil qu'à un fichier image.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

PARTITION_TABLE_OFFSET = 0x8000
PARTITION_TABLE_SIZE = 0xC00
PARTITION_ENTRY_SIZE = 32
PARTITION_MAGIC = b"\xaa\x50"

TYPE_APP = 0x00
TYPE_DATA = 0x01
SUBTYPE_FACTORY = 0x00
SUBTYPE_OTA_MIN = 0x10
SUBTYPE_OTA_MAX = 0x1F
SUBTYPE_OTADATA = 0x00
SUBTYPE_NVS = 0x02

IMAGE_MAGIC = 0xE9
APP_DESC_OFFSET = 0x20  # en-tête image (24 o) + en-tête du premier segment (8 o)
APP_DESC_MAGIC = 0xABCD5432
APP_DESC_SIZE = 256
OTADATA_ENTRY_SIZE = 32
OTA_STATE_VALID = 2
OTA_STATE_INVALID = 3
OTA_STATE_ABORTED = 4

# La chaîne de version du firmware Wanit est dans les données en lecture seule (premier segment).
VERSION_SCAN_LIMIT = 256 * 1024
VERSION_SCAN_CHUNK = 32 * 1024
VERSION_PATTERNS = (
    re.compile(rb"fw_version\x00+(v?\d+\.\d+\.\d+[0-9A-Za-z.+\-]*)\x00"),
    re.compile(rb"ok:(v\d+\.\d+\.\d+[0-9A-Za-z.+\-]*)\x00"),
)
GENERIC_VERSION = re.compile(rb"\x00(v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)\x00")

ReadFn = Callable[[int, int], bytes]


@dataclass(frozen=True)
class Partition:
    label: str
    type: int
    subtype: int
    offset: int
    size: int

    @property
    def is_app(self) -> bool:
        return self.type == TYPE_APP

    @property
    def ota_index(self) -> int | None:
        if self.type == TYPE_APP and SUBTYPE_OTA_MIN <= self.subtype <= SUBTYPE_OTA_MAX:
            return self.subtype - SUBTYPE_OTA_MIN
        return None

    @property
    def is_otadata(self) -> bool:
        return self.type == TYPE_DATA and self.subtype == SUBTYPE_OTADATA

    @property
    def is_nvs(self) -> bool:
        return self.type == TYPE_DATA and self.subtype == SUBTYPE_NVS


def parse_table(data: bytes) -> list[Partition]:
    partitions = []
    for pos in range(0, len(data) - PARTITION_ENTRY_SIZE + 1, PARTITION_ENTRY_SIZE):
        entry = data[pos:pos + PARTITION_ENTRY_SIZE]
        if entry[:2] != PARTITION_MAGIC:
            break  # 0xEBEB (somme MD5) ou 0xFFFF : fin de table
        offset, size = struct.unpack_from("<II", entry, 4)
        label = entry[12:28].split(b"\x00", 1)[0].decode("ascii", "replace")
        partitions.append(Partition(label, entry[2], entry[3], offset, size))
    return partitions


def read_table(read: ReadFn) -> list[Partition]:
    return parse_table(read(PARTITION_TABLE_OFFSET, PARTITION_TABLE_SIZE))


# --- Firmware présent dans un slot -----------------------------------------------------

@dataclass
class AppInfo:
    present: bool
    version: str | None = None
    elf_sha256: str | None = None
    legacy: bool = False

    @property
    def label(self) -> str:
        if not self.present:
            return "vide"
        if self.legacy:
            return "legacy BLE"
        if self.version:
            return self.version
        return f"version inconnue (ELF {self.elf_sha256[:8]})" if self.elf_sha256 else "version inconnue"


def read_app(read: ReadFn, offset: int, size: int) -> AppInfo:
    head = read(offset, APP_DESC_OFFSET + APP_DESC_SIZE)
    if len(head) < 24 or head[0] != IMAGE_MAGIC or not 0 < head[1] <= 16:
        return AppInfo(False)
    desc = head[APP_DESC_OFFSET:]
    elf_sha = idf_ver = None
    if len(desc) >= APP_DESC_SIZE and struct.unpack_from("<I", desc, 0)[0] == APP_DESC_MAGIC:
        idf_ver = desc[112:144].split(b"\x00", 1)[0]
        elf_sha = desc[144:176].hex()
    if elf_sha and elf_sha == _legacy_elf_sha256():
        return AppInfo(True, None, elf_sha, legacy=True)

    # Ne parcourt que le premier segment (données en lecture seule), par blocs, et s'arrête au premier résultat.
    (first_segment,) = struct.unpack_from("<I", head, 24 + 4)
    limit = min(size, 24 + 8 + first_segment, VERSION_SCAN_LIMIT)
    start = APP_DESC_OFFSET + APP_DESC_SIZE
    seen = b""
    fallback = None
    for pos in range(start, limit, VERSION_SCAN_CHUNK):
        seen = seen[-64:] + read(offset + pos, min(VERSION_SCAN_CHUNK, limit - pos))
        for pattern in VERSION_PATTERNS:
            match = pattern.search(seen)
            if match:
                return AppInfo(True, match.group(1).decode("ascii"), elf_sha)
        if fallback is None:
            fallback = next((m.group(1) for m in GENERIC_VERSION.finditer(seen) if m.group(1) != idf_ver), None)
    return AppInfo(True, fallback.decode("ascii") if fallback else None, elf_sha)


@lru_cache(maxsize=1)
def _legacy_elf_sha256() -> str | None:
    try:
        from . import legacy

        with open(legacy.path(), "rb") as fh:
            fh.seek(0x10000 + APP_DESC_OFFSET)
            desc = fh.read(APP_DESC_SIZE)
        if struct.unpack_from("<I", desc, 0)[0] == APP_DESC_MAGIC:
            return desc[144:176].hex()
    except (OSError, struct.error):
        pass
    return None


def file_reader(path) -> ReadFn:
    def read(offset: int, length: int) -> bytes:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read(length)
    return read


# --- otadata et choix du bootloader ---------------------------------------------------

@dataclass
class OtaEntry:
    seq: int
    state: int
    valid: bool


def parse_otadata(data: bytes) -> list[OtaEntry]:
    entries = []
    for sector in (0, len(data) // 2):
        raw = data[sector:sector + OTADATA_ENTRY_SIZE]
        if len(raw) < OTADATA_ENTRY_SIZE:
            continue
        seq, state, crc = struct.unpack_from("<I", raw, 0)[0], *struct.unpack_from("<II", raw, 24)
        valid = (
            seq != 0xFFFFFFFF
            and crc == zlib.crc32(raw[:4], 0xFFFFFFFF)
            and state not in (OTA_STATE_INVALID, OTA_STATE_ABORTED)
        )
        entries.append(OtaEntry(seq, state, valid))
    return entries


def otadata_selecting(ota_index: int, size: int) -> bytes:
    """Contenu complet d'une partition otadata qui désigne le slot `ota_<ota_index>`.

    Le bootloader prend l'entrée valide de plus grand numéro de séquence et démarre
    ota_[(seq - 1) % nombre de slots] : seq = index + 1 convient quel que soit ce nombre.
    L'état VALID indique que l'image est validée : le firmware n'a pas à la confirmer,
    et le mécanisme de rollback d'ESP-IDF ne la rejettera pas au démarrage.
    """
    seq = ota_index + 1
    entry = struct.pack("<I", seq) + b"\xff" * 20 + struct.pack("<II", OTA_STATE_VALID, zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF))
    return entry.ljust(size, b"\xff")  # la seconde copie (secteur suivant) reste effacée


@dataclass
class Slot:
    partition: Partition
    app: AppInfo
    selected: bool = False  # désigné par l'otadata
    boots: bool = False  # celui que le bootloader lancera réellement


@dataclass
class DeviceReport:
    slots: list[Slot] = field(default_factory=list)
    otadata_erased: bool = False
    note: str | None = None

    @property
    def boot(self) -> Slot | None:
        return next((s for s in self.slots if s.boots), None)

    def lines(self) -> list[str]:
        out = []
        for slot in self.slots:
            p = slot.partition
            mark = " ← démarre" if slot.boots else (" (désigné par l'otadata)" if slot.selected else "")
            out.append(f"{p.label} @0x{p.offset:X} : {slot.app.label}{mark}")
        if self.note:
            out.append(self.note)
        return out


def inspect(read: ReadFn, table: list[Partition] | None = None) -> DeviceReport:
    """Reproduit le choix du bootloader ESP-IDF à partir de la flash lue."""
    table = table if table is not None else read_table(read)
    apps = [p for p in table if p.is_app]
    otas = sorted((p for p in apps if p.ota_index is not None), key=lambda p: p.subtype)
    factory = next((p for p in apps if p.subtype == SUBTYPE_FACTORY), None)
    slots = {p.label: Slot(p, read_app(read, p.offset, p.size)) for p in apps}
    report = DeviceReport(list(slots.values()))

    otadata_part = next((p for p in table if p.is_otadata), None)
    selected: Partition | None = None
    if otas and otadata_part:
        entries = [e for e in parse_otadata(read(otadata_part.offset, otadata_part.size)) if e.valid]
        if entries:
            seq = max(e.seq for e in entries)
            selected = otas[(seq - 1) % len(otas)]
        else:
            report.otadata_erased = True
    if selected is None:
        selected = factory or (otas[0] if otas else None)
    if selected is None:
        report.note = "Aucune partition applicative dans la table."
        return report
    slots[selected.label].selected = True

    # Ordre d'essai du bootloader : slot choisi, puis les précédents, factory, puis les suivants.
    order = [selected]
    if selected in otas:
        index = otas.index(selected)
        order += list(reversed(otas[:index])) + ([factory] if factory else []) + otas[index + 1:]
    else:
        order += otas
    boot = next((p for p in order if slots[p.label].app.present), None)
    if boot is None:
        report.note = "Aucun firmware valide : l'appareil ne démarrera pas."
        return report
    slots[boot.label].boots = True
    if boot is not selected:
        report.note = f"L'otadata désigne {selected.label}, vide : le bootloader se rabat sur {boot.label}."
    return report
