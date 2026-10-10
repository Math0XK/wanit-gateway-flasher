"""Détection des ports série et identification des adaptateurs connus."""

from __future__ import annotations

from dataclasses import dataclass

from serial.tools import list_ports

ESPRESSIF_VID = 0x303A
# Ponts USB-série courants sur les cartes ESP32
BRIDGE_VIDS = {
    0x10C4: "Adaptateur CP210x",
    0x1A86: "Adaptateur CH340/CH9102",
    0x0403: "Module de flash (FTDI)",  # module fourni par Wanit : USB vers connecteur Segger
}


@dataclass(frozen=True)
class Port:
    device: str
    description: str
    kind: str  # "espressif", "bridge" ou "other"

    @property
    def label(self) -> str:
        return f"{self.device} — {self.description}"

    @property
    def rank(self) -> int:
        return {"espressif": 0, "bridge": 1}.get(self.kind, 2)


def scan() -> list[Port]:
    ports = []
    for info in list_ports.comports():
        if info.vid == ESPRESSIF_VID:
            kind, desc = "espressif", "ESP32 (USB natif)"
        elif info.vid in BRIDGE_VIDS:
            kind, desc = "bridge", BRIDGE_VIDS[info.vid]
        elif info.vid is None:
            # Ports sans USB (Bluetooth, ports matériels) : peu probables pour un ESP32
            continue
        else:
            kind, desc = "other", info.description or "Port série"
        ports.append(Port(info.device, desc, kind))
    return sorted(ports, key=lambda p: (p.rank, _natural_key(p.device)))


def _natural_key(device: str):
    digits = "".join(c for c in device if c.isdigit())
    return (device.rstrip("0123456789"), int(digits) if digits else 0)
