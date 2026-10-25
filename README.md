# Vox

[![Release](https://github.com/quentinvled/vox/actions/workflows/release.yml/badge.svg)](https://github.com/quentinvled/vox/actions/workflows/release.yml)
[![Dernière version](https://img.shields.io/github/v/release/quentinvled/vox)](https://github.com/quentinvled/vox/releases/latest)

Dictée vocale globale : maintiens **Ctrl + Maj**, parle, relâche — le texte
s'écrit tout seul dans la fenêtre active. Disponible sur **Windows**, **macOS**
(Apple Silicon) et **Linux**.

Pendant l'écoute, **Espace** bascule en **mode consigne** (la pilule passe du
bleu au violet) : ta voix n'est plus retranscrite mais **exécutée** par le
modèle — dis « écris un message poli pour dire que je suis désolé du retard,
je m'en occupe demain » et le texte inséré est le message fini. Un texte copié
(presse-papier) sert alors de matière à transformer : « rends ce texte plus
concis » réécrit ce qui est dans le presse-papier. Un nouvel appui sur Espace
revient à la dictée normale.

Le texte n'est jamais perdu : la pilule reste affichée pendant tout le
traitement et ne s'efface qu'une fois l'insertion **confirmée** (la fenêtre
active est la même qu'à l'arrêt de l'écoute). Sans confirmation, elle reste à
l'écran avec le texte prêt à **copier** ou **réinsérer** depuis le menu de
l'icône.

Transcription via OpenRouter (Whisper, Qwen-ASR, Gemini…), nettoyage LLM
actif par défaut (ton « Nettoyer », désactivable d'un clic dans la pilule),
statistiques d'usage et historique audio local. Les fichiers importés (appels,
réunions, vocaux) sont transcrits, diarisés puis nettoyés automatiquement, avec
transcript cliquable et export md/txt/srt/vtt/json — import par le bouton
« ☰ » de la pilule, « Importer des fichiers audio… » dans le menu de l'icône,
un glisser-déposer de plusieurs fichiers dans la bibliothèque, ou l'onglet
**Réglages → Importer** (modèle, diarisation, nombre de personnes, estimation
du coût, progression en arrière-plan).
L'état des entrées audio (micro, son du système, applications en cours) est
visible dans la zone de notification, testé en continu. Un clic sur
**« Enregistrer un appel (micro + son système)… »** capture l'appel en deux
pistes — il est transcrit puis rangé dans la bibliothèque.

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

### macOS

1. Télécharge **`Vox-<version>-macos-arm64.dmg`** (Mac Apple Silicon, M1 et
   suivants).
2. Ouvre le `.dmg`, glisse **Vox** dans le dossier **Applications**.
3. Au premier lancement, macOS affiche « Vox ne peut pas être ouvert car il ne
   provient pas d'un développeur identifié » (l'application n'est pas signée) :
   clic droit sur Vox → **Ouvrir**, puis **Ouvrir quand même**. Une seule fois.
4. Vox apparaît dans la **barre des menus**, en haut à droite (pas de Dock).
5. Accorde les deux autorisations demandées dans
   **Réglages Système → Confidentialité et sécurité** :
   - **Accessibilité** — pour écrire le texte dans les autres applications ;
   - **Surveillance de la saisie** — pour entendre le raccourci clavier.

   Sans elles, Vox se lance mais n'écrit rien. Relance Vox après les avoir
   accordées. La section **« Autorisations macOS »** de l'onglet **Sortie**
   rappelle leur état et ouvre directement le bon panneau.

Le son du système (enregistrement d'appel) n'est pas captable nativement sous
macOS : il faudrait installer un pilote de boucle comme
[BlackHole](https://existential.audio/blackhole/). La dictée, elle, n'en a pas
besoin.

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
   chevron `^` ; sur macOS, dans la barre des menus en haut à droite).
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
- **macOS** : glisse **Vox** de *Applications* à la corbeille, puis
  `/Applications/Vox.app/Contents/MacOS/Vox --uninstall` pour retirer le
  démarrage automatique et, si tu le souhaites, tes données.
- **Linux** : supprime simplement l'AppImage, puis
  `./Vox-*.AppImage --uninstall` pour retirer le démarrage automatique et,
  si tu le souhaites, tes données.

## Données et confidentialité

Tout est local :

- Windows : `%LOCALAPPDATA%\Vox`
- macOS : `~/Library/Application Support/Vox`
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
uv run python tools/build_macos.py                 # macOS   : Vox.app + .zip + .dmg
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
