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
        │  construit Vox.exe + l'installeur sur windows-latest
        │  genere version.json (manifeste)
        ▼
Release GitHub v0.2.0
        │  assets : Vox-Setup-0.2.0.exe (+ .sha256), version.json
        ▼
L'application interroge l'URL stable :
  .../releases/latest/download/version.json
  → si plus recent : entree « Mise a jour disponible » dans le menu
```

- **`__version__` dans `src/vox/__init__.py` est la source unique.**
  `pyproject.toml` la lit automatiquement (`dynamic = ["version"]`).
- L'URL du manifeste est pre-remplie dans les reglages (`config.DEFAULT_MANIFEST_URL`).
- La CI verifie que le tag correspond bien a `__version__` avant de construire.

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
# Sur une machine Windows, apres avoir bumpe __version__ :
uv sync --group dev
uv run python tools/build_installer.py --rebuild
# -> dist-share/Vox-Setup-<version>.exe (+ .sha256)

uv run python tools/make_manifest.py \
  --version <version> \
  --url https://github.com/quentinvled/vox/releases/download/v<version>/Vox-Setup-<version>.exe \
  --notes "Ce qui change" \
  --out dist-share/version.json
# puis creer la release et y joindre ces trois fichiers.
```

## Important : depot public ou non

Le depot est **prive** aujourd'hui. Or l'application (et tes amis) telecharge
les fichiers **sans compte GitHub** : avec un depot prive, GitHub renvoie une
erreur 404 sur les releases et sur le manifeste. Deux options :

1. **Rendre le depot public** (le plus simple) : tout fonctionne sans compte,
   sans cout, sans configuration. Le code devient visible, mais l'audit montre
   qu'il ne contient aucun secret.
2. **Garder le code prive** et heberger les binaires ailleurs (bucket public
   type Cloudflare R2 / S3) : il faut alors adapter le workflow et pointer
   `DEFAULT_MANIFEST_URL` vers cet hebergement.

## Avertissement Windows (SmartScreen)

L'executable n'est pas signe : au premier lancement, Windows peut afficher
« Windows a protege votre PC ». L'utilisateur clique sur *Informations
complementaires* → *Executer quand meme*. Pour supprimer cet avertissement, il
faut un certificat de signature de code (voir la discussion sur Azure Artifact
Signing / certificat OV). A prevoir quand l'audience grandira.
