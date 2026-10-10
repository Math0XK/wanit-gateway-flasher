# Composants tiers

L'exécutable `WanitGatewayFlasher.exe` intègre les composants suivants.

| Composant | Licence | Source |
|---|---|---|
| esptool (Espressif Systems) | GPL-2.0-or-later | https://github.com/espressif/esptool |
| CustomTkinter | CC0-1.0 | https://github.com/TomSchimansky/CustomTkinter |
| pySerial | BSD-3-Clause | https://github.com/pyserial/pyserial |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow |
| cryptography (dépendance d'esptool) | Apache-2.0 ou BSD-3-Clause | https://github.com/pyca/cryptography |
| Python et Tcl/Tk | PSF-2.0, licence Tcl/Tk | https://www.python.org |

L'exécutable est produit avec PyInstaller (GPL-2.0-or-later avec exception pour le bootloader), qui n'impose pas de licence au programme construit.

Le code source complet correspondant à chaque version publiée est disponible dans ce dépôt, au tag de la release.
