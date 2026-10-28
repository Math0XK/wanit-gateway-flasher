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
SHA256 = "7cc5a72a78bbf02435c17ba0c5ac95d7a3b463c870e97c8f53683be7da443d24"
SIZE = 831264
BLE_NAME = "Wanit SC"


def path() -> Path:
    return resource(f"legacy/{FILE_NAME}")
