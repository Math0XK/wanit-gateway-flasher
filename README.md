# Wanit Gateway Flasher

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-green)

Utilitaire Windows pour flasher le firmware du **Wanit Gateway** (ESP32-C6) avec le module de flash USB vers Segger fourni par Wanit, à partir des binaires publiés sur [wanit-gateway-releases](https://github.com/Math0XK/wanit-gateway-releases/releases). Il s'adresse aux intégrateurs et au dépannage : aucune installation, aucune ligne de commande.

## Télécharger

**[⬇ Télécharger WanitGatewayFlasher.exe](https://github.com/Math0XK/wanit-gateway-flasher/releases/latest/download/WanitGatewayFlasher.exe)** (Windows 10 et 11, 64 bits)

Ce lien pointe toujours vers la dernière version. Guide d'installation imprimable : [Guide-Wanit-Gateway-Flasher.pdf](docs/Guide-Wanit-Gateway-Flasher.pdf).

## Utilisation

1. Lancez `WanitGatewayFlasher.exe`. Il n'y a rien à installer.
2. Branchez le module de flash sur le connecteur Segger du Wanit Gateway, puis sur un port USB du PC. Il est détecté et sélectionné automatiquement ; il passe seul la carte en mode flash, sans bouton à manipuler.
3. La dernière version du firmware est proposée par défaut. Vous pouvez choisir une version plus ancienne, ou un fichier `.bin` local.
4. Cliquez sur **Flasher**.

L'appareil redémarre à la fin. S'il n'est pas encore provisionné, il apparaît en BLE sous le nom `Wanit SC`. Le Wi-Fi provisionné, l'UID, le nom et le token sont conservés, sauf si vous activez l'effacement complet.

### Avertissements de Windows : c'est normal

L'exécutable n'est pas signé avec un certificat de code payant. Windows et les navigateurs se méfient donc d'un programme qu'ils ne connaissent pas encore. Le fichier est publié directement sur GitHub, et son empreinte SHA-256 est affichée sur la page de chaque release. Pour vérifier un fichier téléchargé, comparez-la au résultat de cette commande PowerShell :

```powershell
Get-FileHash .\WanitGatewayFlasher.exe
```

- **Au téléchargement (Edge, Chrome)** : si le fichier est signalé comme « peu courant », ouvrez le menu **…** à côté du téléchargement, puis **Conserver** → **Afficher plus** → **Conserver quand même**.
- **Au premier lancement** (« Windows a protégé votre ordinateur ») : cliquez sur **Informations complémentaires**, vérifiez que le fichier s'appelle bien `WanitGatewayFlasher.exe`, puis **Exécuter quand même**.

Ces avertissements n'apparaissent qu'une fois : ensuite, l'utilitaire se met à jour lui-même.

### Options avancées (roue crantée)

| Option | Effet |
|---|---|
| Variante debug | Flashe `-full-debug.bin` (logs verbeux). Expose des informations sensibles sur l'UART : pas pour la production. |
| Effacer toute la flash | Efface toute la mémoire avant d'écrire : Wi-Fi, UID, nom et token sont perdus. |
| Vitesse de transfert | 921600 bauds par défaut. Baissez-la si le flash échoue en cours d'écriture (rallonge USB, hub). |

### Sécurités intégrées

- Seules les releases contenant une image complète (`-full.bin`, v1.2.0 et suivantes) sont proposées.
- Chaque binaire téléchargé est vérifié par son empreinte SHA-256 publiée par GitHub.
- Un fichier local est contrôlé avant le flash : image complète (bootloader et table de partitions présents) et compilée pour ESP32-C6. Les images OTA (`-ota.bin`) sont refusées.
- Le flash s'arrête avant toute écriture si la puce détectée n'est pas un ESP32-C6.

### Sans connexion Internet

- Les versions déjà flashées une fois restent en cache (`%LOCALAPPDATA%\WanitGatewayFlasher`) et sont proposées hors ligne.
- Sans cache, l'onglet **Fichier local** permet de flasher un `.bin` récupéré par ailleurs.

### Mises à jour de l'utilitaire

Au démarrage, l'utilitaire vérifie s'il existe une version plus récente. Une bannière propose alors de se mettre à jour : un clic télécharge la nouvelle version, vérifie son empreinte, remplace l'exécutable et le relance. Rien ne se fait sans votre accord, et jamais pendant un flash.

### En cas d'échec

Le message d'erreur indique quoi faire. Le cas le plus courant : le connecteur du module est mal enfoncé ou à l'envers sur le connecteur Segger de la carte. Rebranchez-le et relancez. Si aucun port n'apparaît au premier branchement, laissez à Windows le temps d'installer le pilote FTDI, ou installez le pilote « VCP » depuis ftdichip.com. Le journal détaillé (bouton **Journal**, puis **Copier**) est utile pour le support.

## Développement

Prérequis : Python 3.13 ou plus récent.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m wanit_flasher
```

| Fichier | Rôle |
|---|---|
| `wanit_flasher/app.py` | Interface (CustomTkinter) |
| `wanit_flasher/flasher.py` | Validation des images et flash via l'API esptool |
| `wanit_flasher/github.py` | Releases GitHub, téléchargement vérifié, cache |
| `wanit_flasher/updater.py` | Auto-mise à jour de l'exécutable |
| `wanit_flasher/ports.py` | Détection des ports série |
| `wanit_flasher/theme.py` | Couleurs de la charte Wanit |
| `tools/make_assets.py` | Génère icône et logos depuis `assets/brand/wanit-logo.jpg` |
| `tools/build.py` | Construit `dist/WanitGatewayFlasher.exe` |
| `tools/build_guide.py` | Génère le guide PDF depuis `docs/guide/guide.html` |

### Construire l'exécutable

La CI installe les dépendances depuis `requirements-build.lock`, qui fige toutes les versions (y compris indirectes) avec leurs empreintes :

```bash
pip install --require-hashes --no-deps -r requirements-build.lock
```

```bash
python tools/build.py
```

Après toute modification de `requirements.txt` ou de `requirements-build.in`, régénérez le fichier verrouillé :

```bash
uv pip compile requirements-build.in --python-version 3.13 --python-platform x86_64-pc-windows-msvc --generate-hashes --no-header -o requirements-build.lock
```

### Publier une version

1. Incrémentez `__version__` dans `wanit_flasher/__init__.py` et committez.
2. Créez et poussez le tag correspondant :

```bash
git tag v1.0.1
```

```bash
git push origin v1.0.1
```

Le workflow GitHub Actions construit l'exe et publie la release. Les utilisateurs se voient proposer la mise à jour à leur prochain lancement, et le lien de téléchargement pointe automatiquement vers la nouvelle version.

Si le build ne s'est pas lancé, ou pour reconstruire une release existante : onglet *Actions* → *Release* → *Run workflow*, en indiquant le tag (par exemple `v1.0.1`). Le tag doit déjà exister sur GitHub et correspondre à `__version__`.

### Guide PDF

Le guide `docs/Guide-Wanit-Gateway-Flasher.pdf` est généré à partir de `docs/guide/guide.html` (Windows, Microsoft Edge requis) :

```bash
python tools/build_guide.py
```

### Page de téléchargement

La page `docs/index.html` est servie par GitHub Pages (*Settings* → *Pages* → *Deploy from a branch* → `main`, dossier `/docs`). Elle n'a pas besoin d'être modifiée à chaque version : le bouton utilise le lien « dernière version » et le numéro de version est lu depuis l'API GitHub.

## Licence

Ce programme est distribué sous licence **GNU GPL v3 ou ultérieure** (voir [`LICENSE`](LICENSE)). Ce choix découle d'esptool (GPL v2 ou ultérieure), qui est intégré à l'exécutable. Les licences des autres composants sont listées dans [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Le nom et le logo **Wanit** sont la propriété de Wanit et ne sont pas couverts par cette licence. Le firmware flashé est distribué séparément, selon les termes de [son propre dépôt](https://github.com/Math0XK/wanit-gateway-releases).
