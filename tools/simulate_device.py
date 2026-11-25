"""Lance le flasher avec un ESP32-C6 simulé, sans matériel.

L'appareil virtuel reproduit le cas du banc : app0 contient la dernière release, app1 une ancienne
version installée par OTA (le numéro est remplacé par v2.0.0 dans une copie du firmware), et
l'otadata désigne app1. Après chaque redémarrage simulé, le firmware « fait un rollback » : il
réécrit l'otadata vers l'autre slot, ce qui est le pire cas pour le flasher.

Usage :
    python tools/simulate_device.py                 # interface, à piloter à la main
    python tools/simulate_device.py --auto          # enchaîne Versions → Flasher → Versions
    python tools/simulate_device.py --sans-correctif  # ancien comportement (app1, otadata et NVS), pour comparer
    python tools/simulate_device.py --auto --captures docs/guide/img   # écrit aussi les captures
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from esptool.util import FatalError  # noqa: E402

from wanit_flasher import FIRMWARE_REPO, app, flasher, github, partitions, ports  # noqa: E402

PORT = ports.Port("COM99", "Module de flash (simulé)", "bridge")
MAC = "40:4c:ca:00:be:ef"
FLASH_SIZE = 0x400000
OLD_VERSION = b"v2.0.0"
NVS_MARK = b"WIFI=Atelier;UID=GW-0042;TOKEN=simule"  # remplace les réglages d'un appareil provisionné


def otadata_entry(seq: int) -> bytes:
    crc = zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF)
    return struct.pack("<I", seq) + b"\xff" * 20 + struct.pack("<II", 0xFFFFFFFF, crc)


class SimulatedEsp:
    """Le strict nécessaire de l'API esptool utilisée par le flasher, sur une flash en mémoire."""

    CHIP_NAME = "ESP32-C6"
    IS_STUB = True

    def __init__(self, image: bytes, rollback: bool) -> None:
        self.flash = bytearray(b"\xff" * FLASH_SIZE)
        self.rollback = rollback
        self.just_flashed = False
        self.fail_next = False
        self.flash[:len(image)] = image
        table = partitions.parse_table(image[partitions.PARTITION_TABLE_OFFSET:][:partitions.PARTITION_TABLE_SIZE])
        app0 = next(p for p in table if p.ota_index == 0)
        app1 = next(p for p in table if p.ota_index == 1)
        app = bytearray(image[app0.offset:])
        version = partitions.read_app(lambda o, n: image[o:o + n], app0.offset, app0.size).version
        if version:
            app = app.replace(version.encode() + b"\x00", OLD_VERSION.ljust(len(version), b"\x00") + b"\x00")
        self.flash[app1.offset:app1.offset + len(app)] = app
        otadata = next(p for p in table if p.is_otadata)
        self.flash[otadata.offset:otadata.offset + otadata.size] = b"\xff" * otadata.size
        self.flash[otadata.offset:otadata.offset + 32] = otadata_entry(2)  # (2 - 1) % 2 = app1
        nvs = next(p for p in table if p.is_nvs)
        self.nvs = (nvs.offset, nvs.size)
        self.flash[nvs.offset:nvs.offset + len(NVS_MARK)] = NVS_MARK  # appareil provisionné

    def nvs_kept(self) -> bool:
        offset, _ = self.nvs
        return bytes(self.flash[offset:offset + len(NVS_MARK)]) == NVS_MARK

    # Connexion ---------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read_mac(self):
        return bytes(int(b, 16) for b in MAC.split(":"))

    def get_chip_description(self):
        return "ESP32-C6 (QFN40) (revision v0.1) [simulé]"

    # Flash ------------------------------------------------------------------------
    def read_flash(self, offset, length, progress_fn=None):
        time.sleep(length / 90_000)  # ~921600 bauds
        return bytes(self.flash[offset:offset + length])

    def erase_region(self, offset, size):
        time.sleep(0.05 + size / 400_000)
        self.flash[offset:offset + size] = b"\xff" * size

    def write(self, address: int, image: bytes) -> None:
        log = flasher.log
        end = address + len(image) - 1 | 0xFFF
        log.print(f"Flash will be erased from 0x{address:08x} to 0x{end:08x}...")
        log.print(f"Compressed {len(image)} bytes to {len(image) * 6 // 10}...")
        steps = max(1, min(40, len(image) // 40_000))
        for i in range(steps + 1):
            log.progress_bar(i, steps)
            time.sleep(0.08)
        # esptool efface les secteurs entiers touchés, puis écrit
        self.flash[address:end + 1] = b"\xff" * (end + 1 - address)
        self.flash[address:address + len(image)] = image
        self.just_flashed = True
        log.print(f"Wrote {len(image)} bytes at 0x00000000 in 3.4 seconds.")
        log.print("Hash of data verified.")

    def reset(self) -> None:
        """Redémarrage : le bootloader choisit un slot, puis le firmware tente un rollback."""
        report = partitions.inspect(lambda o, n: bytes(self.flash[o:o + n]))
        boot = report.boot
        flasher.log.print(f"[simulation] démarrage sur {boot.partition.label} ({boot.app.label})" if boot else "[simulation] aucun firmware")
        flasher.log.print("[simulation] NVS : réglages présents" if self.nvs_kept() else "[simulation] NVS : réglages PERDUS")
        first_boot, self.just_flashed = self.just_flashed, False
        if not (self.rollback and first_boot and boot):
            return
        table = partitions.parse_table(bytes(self.flash[0x8000:0x8C00]))
        otadata = next(p for p in table if p.is_otadata)
        entries = partitions.parse_otadata(bytes(self.flash[otadata.offset:otadata.offset + otadata.size]))
        valid = [e for e in entries if e.valid]
        seq = max((e.seq for e in valid), default=0)
        other = 1 - (boot.partition.ota_index or 0)
        new_seq = seq + 1
        while (new_seq - 1) % 2 != other:
            new_seq += 1
        # ESP-IDF écrit dans le secteur qui ne porte pas l'entrée la plus récente
        newest_in_first = bool(entries) and entries[0].valid and entries[0].seq == seq
        sector = otadata.offset + (0x1000 if newest_in_first else 0)
        self.flash[sector:sector + 0x1000] = b"\xff" * 0x1000
        self.flash[sector:sector + 32] = otadata_entry(new_seq)
        flasher.log.print("[simulation] le firmware déclenche un rollback : l'otadata désigne maintenant l'autre slot")


def latest_image() -> Path:
    releases, _ = github.firmware_releases(FIRMWARE_REPO)
    release = next(r for r in releases if app.FULL_ASSET in r.assets and not r.prerelease)
    asset = release.assets[app.FULL_ASSET]
    print(f"Image de référence : {release.tag} (téléchargement vérifié)")
    return github.download(asset, github.firmware_path(release, asset), github.expected_sha256(release, asset))


def install(device: SimulatedEsp, fix: bool, port: ports.Port) -> None:
    def fake_open(port_name, baud, on_log):
        time.sleep(0.8)
        if device.fail_next:
            device.fail_next = False
            raise FatalError("Failed to connect to ESP32-C6: No serial data received.")
        on_log(f"Appareil : {device.get_chip_description()} · MAC {MAC}")
        return device, device, MAC

    flasher._open = fake_open
    def fake_write_flash(esp, addr_data, erase_all=False, **_):
        if erase_all:
            flasher.log.print("Erasing flash (this may take a while)...")
            time.sleep(2)
            esp.flash[:] = b"\xff" * FLASH_SIZE
        for address, data in addr_data:
            esp.write(address, Path(data).read_bytes() if isinstance(data, (str, Path)) else bytes(data))

    flasher.write_flash = fake_write_flash
    flasher.reset_chip = lambda esp, mode: esp.reset()
    if not fix:
        flasher._neutralize_stale_slots = lambda *a, **k: None
        flasher._select_boot_slot = lambda *a, **k: None
        flasher._write_plan = lambda image, erase_all, on_log: [(0, image.read_bytes())]
    app.ports.scan = lambda: [port]


def autopilot(window: app.App, device: SimulatedEsp, captures: Path | None) -> None:
    """Joue le scénario du banc ; avec `captures`, écrit au passage les captures du guide."""
    steps = [
        ("capture", "main.png"),
        ("advanced", None), ("capture", "advanced.png"), ("advanced", None),
        ("versions", None), ("wait", None), ("capture", "versions-before.png"),
        ("flash", None), ("capture", "flashing.png"), ("wait", None), ("capture", "success.png"),
        ("back", None),
        ("versions", None), ("wait", None), ("capture", "versions.png"),
    ]
    if captures:
        steps += [("fail", None), ("flash", None), ("wait", None), ("capture", "error.png")]

    def grab(name: str) -> None:
        if not captures:
            return
        from PIL import ImageGrab

        window.lift()
        window.update()
        x, y = window.winfo_rootx(), window.winfo_rooty()
        ImageGrab.grab((x, y, x + window.winfo_width(), y + window.winfo_height()), all_screens=True).save(captures / name)
        print(f"Capture : {captures / name}")

    def tick() -> None:
        if window.releases_state == "loading" or (steps and steps[0][0] == "wait" and window.busy):
            window.after(300, tick)
            return
        if not steps:
            print("Simulation terminée : la fenêtre reste ouverte.")
            return
        kind, arg = steps.pop(0)
        delay = 2500
        if kind == "versions":
            print("→ clic sur « Versions »")
            window._start_read_versions()
        elif kind == "flash":
            print("→ clic sur « Flasher »")
            window._start_flash()
        elif kind == "back":
            print("→ retour au formulaire")
            window._back_to_form()
        elif kind == "advanced":
            window._toggle_advanced()
            delay = 800
        elif kind == "fail":
            print("→ connecteur débranché (échec simulé)")
            device.fail_next = True
            delay = 100
        elif kind == "capture":
            grab(arg)
            delay = 600
        else:
            delay = 300
        window.after(delay, tick)

    window.after(1500, tick)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--auto", action="store_true", help="enchaîne seul Versions → Flasher → Versions")
    parser.add_argument("--sans-correctif", action="store_true", help="désactive la neutralisation d'app1, la réécriture de l'otadata et la conservation de la NVS")
    parser.add_argument("--sans-rollback", action="store_true", help="le firmware ne déclenche pas de rollback au démarrage")
    parser.add_argument("--captures", type=Path, help="dossier où écrire les captures (avec --auto)")
    args = parser.parse_args()

    image = latest_image()
    device = SimulatedEsp(image.read_bytes(), rollback=not args.sans_rollback)
    # Pour les captures du guide : même port et même échelle que les captures existantes
    port = ports.Port("COM5", "Module de flash (FTDI)", "bridge") if args.captures else PORT
    install(device, fix=not args.sans_correctif, port=port)

    import customtkinter as ctk

    ctk.set_appearance_mode("light")
    if args.captures:
        ctk.set_widget_scaling(1.3)
        ctk.set_window_scaling(1.3)
    window = app.App()
    window.title(window.title() + " — SIMULATION")
    if args.auto:
        if args.captures:
            args.captures.mkdir(parents=True, exist_ok=True)
        autopilot(window, device, args.captures)
    window.mainloop()


if __name__ == "__main__":
    main()
