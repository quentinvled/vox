# Publier une version de Vox

Le but : une seule commande pour publier, et des amis qui sont prevenus
automatiquement dans l'application quand une nouvelle version sort.

## En bref

```bash
uv run python tools/release.py 0.2.0
```

C'est tout. Le reste est automatique.

## Comment ca marche

```
tools/release.py 0.2.0
        │  met a jour __version__, commit, tag v0.2.0, push
        ▼
GitHub Actions (.github/workflows/release.yml)
        │  job « verify »   : le tag correspond bien a __version__
        │  job « windows »  : Vox.exe + l'installeur (windows-latest)
        │  job « linux »    : binaire + AppImage (ubuntu-latest)
        │  job « release »  : manifeste multi-plateforme + release GitHub
        ▼
Release GitHub v0.2.0
        │  assets : Vox-Setup-0.2.0.exe (+ .sha256)
        │           Vox-0.2.0-x86_64.AppImage (+ .sha256)
        │           version.json
        ▼
L'application interroge l'URL stable :
  .../releases/latest/download/version.json
  → choisit son fichier selon l'OS (champ « urls »)
  → si plus recent : entree « Mise a jour disponible » dans le menu
```

- **`__version__` dans `src/vox/__init__.py` est la source unique.**
  `pyproject.toml` la lit automatiquement (`dynamic = ["version"]`).
- L'URL du manifeste est pre-remplie dans les reglages (`config.DEFAULT_MANIFEST_URL`).
- La CI verifie que le tag correspond bien a `__version__` avant de construire.
- Le manifeste contient un champ `urls` par systeme ; l'ancien champ `url`
  (Windows) est conserve pour la compatibilite.

## Avant de publier

1. `git pull` et verifie que l'arbre est propre.
2. Mettre a jour le numero dans `tools/release.py 0.2.0` (il doit etre superieur).
3. Verifier qu'aucun secret n'est present :
   ```bash
   git grep -nIE 'sk-or-v1-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}' -- . ':!uv.lock'
   ```
   Le `.env` est ignore par git, ton fichier `%LOCALAPPDATA%\Vox\settings.json`
   ne fait pas partie du depot : rien de sensible ne doit etre commite.

## Suivre / corriger

- Actions : https://github.com/quentinvled/vox/actions
- Releases : https://github.com/quentinvled/vox/releases
- Relancer le build : onglet Actions → « Release » → *Re-run jobs*.
- Le workflow ne se declenche **que** sur un tag `v*`. Un simple `git push`
  sur `main` ne publie rien.

## Publication manuelle (sans la CI)

```bash
# Windows, apres avoir bumpe __version__ :
uv sync --group dev
uv run python tools/build_installer.py --rebuild
# -> dist-share/Vox-Setup-<version>.exe (+ .sha256)

# Linux, sur une machine avec appimagetool :
uv run python tools/build_appimage.py
# -> dist-share/Vox-<version>-x86_64.AppImage (+ .sha256)

uv run python tools/make_manifest.py \
  --version <version> \
  --windows https://github.com/quentinvled/vox/releases/download/v<version>/Vox-Setup-<version>.exe \
  --linux https://github.com/quentinvled/vox/releases/download/v<version>/Vox-<version>-x86_64.AppImage \
  --notes "Ce qui change" \
  --out dist-share/version.json
# puis creer la release et y joindre tous ces fichiers.
```

## Depot public

Le depot est **public** : tes amis telechargent les binaires sans compte
GitHub, et le manifeste de mise a jour est lisible par tous. C'est le
fonctionnement attendu. Le code est visible, mais l'audit initial n'a trouve
aucun secret (le `.env` est ignore par git, les cles ne vivent que dans
`%LOCALAPPDATA%\Vox\settings.json` ou `~/.local/share/Vox/settings.json`).
Seule donnee personnelle exposee : l'adresse e-mail de l'auteur, presente dans
l'historique des commits.

## Avertissement Windows (SmartScreen)

L'executable n'est pas signe : au premier lancement, Windows peut afficher
« Windows a protege votre PC ». L'utilisateur clique sur *Informations
complementaires* → *Executer quand meme*. Pour supprimer cet avertissement, il
faut un certificat de signature de code (voir la discussion sur Azure Artifact
Signing / certificat OV). A prevoir quand l'audience grandira.
