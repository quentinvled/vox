"""Prepare et publie une nouvelle version de Vox en une seule commande.

    uv run python tools/release.py 0.2.0

Ce que fait le script :
  1. verifie que l'arbre git est propre et qu'on est sur `main`
  2. met a jour `__version__` dans src/vox/__init__.py (source unique)
  3. cree le commit « release: vX.Y.Z » puis le tag « vX.Y.Z »
  4. pousse la branche et le tag

Le push declenche ensuite GitHub Actions (.github/workflows/release.yml) qui
construit Vox + l'installeur, cree la release et publie le manifeste de mise a
jour. Rien d'autre a faire.

Variante : `--no-push` cree le commit et le tag localement sans publier.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vox.updates import parse_version  # noqa: E402

INIT_FILE = ROOT / "src" / "vox" / "__init__.py"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def git(*args: str, capture: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=capture, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip() if capture else ""
        raise SystemExit(f"git {' '.join(args)} a echoue. {detail}".strip())
    return (result.stdout or "") if capture else ""


def read_version() -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', INIT_FILE.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("Version introuvable dans src/vox/__init__.py")
    return match.group(1)


def tag_exists(tag: str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Publier une release Vox")
    parser.add_argument("version", help="nouvelle version, ex. 0.2.0")
    parser.add_argument("--no-push", action="store_true", help="commit + tag sans pousser")
    parser.add_argument("--branch", default="main", help="branche a utiliser (defaut : main)")
    args = parser.parse_args()

    new = args.version.strip().lstrip("v")
    if not SEMVER.match(new):
        raise SystemExit(f"Version invalide « {args.version} » : format attendu X.Y.Z")

    if git("status", "--porcelain").strip():
        raise SystemExit("Arbre git non propre : commit ou stash tes changements d'abord.")

    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != args.branch:
        raise SystemExit(f"Branche courante « {branch} », attendu « {args.branch} ».")

    current = read_version()
    if parse_version(new) <= parse_version(current):
        raise SystemExit(f"La version {new} doit etre superieure a la version actuelle {current}.")

    tag = f"v{new}"
    if tag_exists(tag):
        raise SystemExit(f"Le tag {tag} existe deja.")

    INIT_FILE.write_text(
        re.sub(
            r'__version__\s*=\s*"[^"]+"',
            f'__version__ = "{new}"',
            INIT_FILE.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    print(f"Version : {current} -> {new}")

    git("add", str(INIT_FILE.relative_to(ROOT)))
    git("commit", "-m", f"release: v{new}", capture=False)
    git("tag", "-a", tag, "-m", f"Vox {new}", capture=False)

    if args.no_push:
        print(f"Commit et tag {tag} crees localement (--no-push).")
        print(f"Pour publier : git push origin {branch} && git push origin {tag}")
        return 0

    git("push", "origin", branch, capture=False)
    git("push", "origin", tag, capture=False)
    print(f"Tag {tag} pousse : GitHub Actions construit et publie la release.")
    print("Suivi : https://github.com/quentinvled/vox/actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
