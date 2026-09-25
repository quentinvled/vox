# Vox

Dictée vocale globale : maintiens **Ctrl + Maj**, parle, relâche — le texte
s'écrit tout seul dans la fenêtre active. Disponible sur **Windows** et
**Linux**.

Transcription via OpenRouter (Whisper, Qwen-ASR, Gemini…), reformulation LLM
optionnelle, statistiques d'usage et historique audio local.

## Télécharger

Ouvre la page des versions :
**[github.com/quentinvled/vox/releases/latest](https://github.com/quentinvled/vox/releases/latest)**

### Windows

1. Télécharge **`Vox-Setup-<version>.exe`**.
2. Double-clique dessus. Windows affichera peut-être « Windows a protégé votre
   PC » (l'installateur n'est pas signé numériquement) : clique sur
   **Informations complémentaires**, puis **Exécuter quand même**.
3. L'installation se fait dans ton dossier personnel, **sans droits
   administrateur**. Des raccourcis sont créés sur le Bureau et dans le Menu
   Démarrer.

### Linux

1. Télécharge **`Vox-<version>-x86_64.AppImage`**.
2. Rends-le exécutable et lance-le :

   ```bash
   chmod +x Vox-*-x86_64.AppImage
   ./Vox-*-x86_64.AppImage
   ```

   (Double-clic possible aussi, selon ton bureau.)

Si le lancement échoue avec un message sur `libfuse.so.2` (Ubuntu 24.04 et
suivants ne l'installent plus par défaut), deux solutions :

```bash
sudo apt install libfuse2t64          # ou libfuse2 selon la distribution
# ... ou sans rien installer :
./Vox-*-x86_64.AppImage --appimage-extract-and-run
```

Cibles : **X11**. Sous Wayland, le raccourci global et le collage automatique
sont restreints par le compositeur (Wayland interdit à une application
d'injecter des touches dans une autre) ; en cas de souci, passe la méthode
d'insertion sur **Frappe** dans les réglages, ou ouvre une session X11. Sur
GNOME, l'icône de la barre système nécessite l'extension *AppIndicator*.

Selon la distribution, quelques bibliothèques système peuvent manquer
(typiquement `libxcb-cursor0`, `libxkbcommon-x11-0`, `libgl1`, `libportaudio2`).

Un fichier `.sha256` est publié à côté de chaque fichier pour vérifier
l'intégrité du téléchargement.

## Premier lancement

1. Une petite icône apparaît près de l'horloge (sur Windows, derrière le
   chevron `^`).
2. Ouvre les réglages (clic droit sur l'icône → **Réglages**).
3. Colle une **clé OpenRouter** (à créer sur
   [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys), après
   avoir ajouté quelques dollars de crédit — la dictée coûte ~0,10 $/heure).
4. Clique sur **Tester**, puis **Enregistrer**.
5. Maintiens **Ctrl + Maj**, parle, relâche : le texte s'insère.

## Mises à jour

Vox vérifie automatiquement les nouvelles versions au démarrage et deux fois
par jour. Quand une version plus récente est publiée, une notification et une
entrée « Mise à jour disponible » apparaissent dans le menu : un clic ouvre la
page de téléchargement. Rien n'est installé automatiquement. Le bon fichier est
choisi automatiquement selon ton système.

## Désinstaller

- **Windows** : Paramètres → Applications → Vox → Désinstaller.
- **Linux** : supprime simplement l'AppImage, puis
  `./Vox-*.AppImage --uninstall` pour retirer le démarrage automatique et,
  si tu le souhaites, tes données.

## Données et confidentialité

Tout est local :

- Windows : `%LOCALAPPDATA%\Vox`
- Linux : `~/.local/share/Vox`

Seul l'audio de chaque dictée est envoyé au service de transcription choisi,
via OpenRouter.

## Développement

```bash
uv sync
uv run vox                 # lance l'application
uv run vox --list-devices  # liste les micros
uv run vox --stats         # statistiques en ligne de commande
```

Construire les paquets :

```bash
uv run python tools/build_installer.py --rebuild   # Windows : Vox-Setup-<version>.exe
uv run python tools/build_appimage.py              # Linux   : Vox-<version>-x86_64.AppImage
```

Publier une version (voir [`RELEASING.md`](RELEASING.md)) :

```bash
uv run python tools/release.py 0.2.0
```
