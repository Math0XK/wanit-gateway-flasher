"""Firmware legacy (Bluetooth uniquement), embarqué dans l'exécutable pour les retours en arrière.

Cette version ne recevra plus de mise à jour : le binaire est donc figé ici, avec son empreinte,
plutôt que publié dans une release. Il fonctionne hors ligne.
"""

from __future__ import annotations

from pathlib import Path

from .paths import resource

LABEL = "Legacy — BLE uniquement (retour en arrière)"
SHORT_NAME = "legacy BLE"
FILE_NAME = "esp32-ble-serial-legacy-full.bin"
SHA256 = "02e744d33d26b2ea13a77357c9c9b9a80e752b22af6281b901da8df3b67af4b7"
SIZE = 831184
BLE_NAME = "Wanit SC"


def path() -> Path:
    return resource(f"legacy/{FILE_NAME}")
