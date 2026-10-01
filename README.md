# Vox

[![Release](https://github.com/quentinvled/vox/actions/workflows/release.yml/badge.svg)](https://github.com/quentinvled/vox/actions/workflows/release.yml)
[![Dernière version](https://img.shields.io/github/v/release/quentinvled/vox)](https://github.com/quentinvled/vox/releases/latest)

Dictée vocale globale : maintiens **Ctrl + Maj**, parle, relâche — le texte
s'écrit tout seul dans la fenêtre active. Disponible sur **Windows** et
**Linux**.

Transcription via OpenRouter (Whisper, Qwen-ASR, Gemini…), nettoyage LLM
actif par défaut (ton « Nettoyer », désactivable d'un clic dans la pilule),
statistiques d'usage et historique audio local. Les fichiers importés (appels,
réunions, vocaux) sont transcrits, diarisés puis nettoyés automatiquement, avec
transcript cliquable et export md/txt/srt/vtt/json.

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

Vox vérifie les nouvelles versions au démarrage et deux fois par jour. Quand
une version plus récente est publiée, une notification et une entrée « Mise à
jour disponible » apparaissent dans le menu. L'onglet **Mises à jour** des
réglages affiche alors un bouton **« Mettre à jour maintenant »** : en un clic,
Vox télécharge la nouvelle version (dans un dossier privé, jamais dans tes
Téléchargements), l'installe et redémarre. Rien ne s'exécute sans ton clic.

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
uv run vox --import appel.m4a                     # transcription + diarisation
uv run vox --import appel.m4a --import-no-save    # export seul, sans bibliothèque
uv run vox --library                              # liste les imports transcrits
uv run vox --import appel.m4a --import-model x-ai/grok-stt-1.0
uv run pytest              # tests
```

Dans l'application, le bouton **Importer** de la Bibliothèque fait la même
chose avec la progression à l'écran ; le transcript s'y relit réplique par
réplique, les locuteurs s'y renomment, et les exports (md, txt, srt, vtt, json)
se font en un clic.

Construire les paquets :

```bash
uv run python tools/build_installer.py --rebuild   # Windows : Vox-Setup-<version>.exe
uv run python tools/build_appimage.py              # Linux   : Vox-<version>-x86_64.AppImage
```

Publier une version (voir [`RELEASING.md`](RELEASING.md)) :

```bash
uv run python tools/release.py 0.2.0
```

## Conception et suivi

- [`docs/roadmap.md`](docs/roadmap.md) — feuille de route et suivi de
  l'avancement (lot en cours, prochains tests).
- [`docs/assistant-audio.md`](docs/assistant-audio.md) — Vox comme assistant
  audio global : enregistrement micro/système, détection d'appels, bibliothèque,
  transcription live, agent.
- [`docs/modeles-diarisation.md`](docs/modeles-diarisation.md) — diarisation :
  modèles OpenRouter, options par fournisseur, limites et coûts.
