"""Point d'entree : `python -m vox` ou `vox`.

Les imports Qt sont volontairement differes : les commandes en ligne de
commande (dont `--uninstall`) n'ont pas besoin de charger PySide6.
"""

from __future__ import annotations

import logging
import platform
import sys

from . import __version__
from .paths import log_file

log = logging.getLogger("vox")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s\t%(levelname)s\t%(message)s",
        handlers=[logging.FileHandler(log_file(), encoding="utf-8")],
    )


def main() -> int:
    """Point d'entree unique : CLI si des options sont passees, sinon UI."""
    cli_flags = {
        "--list-models",
        "--list-devices",
        "--test-key",
        "--transcribe",
        "--stats",
        "--uninstall",
        "--where",
        "--check-update",
        "--write-manifest",
        "--set-manifest-url",
        "--version",
        "-h",
        "--help",
    }
    if cli_flags.intersection(sys.argv[1:]):
        return main_cli()
    return main_gui()


def main_gui() -> int:
    """Lance l'interface graphique."""
    from PySide6.QtWidgets import QApplication

    from .app import VoxApp, acquire_single_instance
    from .ui.widgets import make_app_icon

    _configure_logging()
    log.info(
        "Vox %s — demarrage (fige=%s, arch=%s)",
        __version__,
        getattr(sys, "frozen", False),
        platform.machine(),
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Vox")
    app.setApplicationDisplayName("Vox")
    app.setOrganizationName("Vox")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_app_icon())

    server = acquire_single_instance(app)
    if server is None:
        log.info("Instance deja active : sortie.")
        return 0

    vox = VoxApp(app)
    server.newConnection.connect(lambda: vox.overlay.pin(4))
    vox.start()

    try:
        return app.exec()
    finally:
        vox.pipeline.shutdown()
        log.info("Vox — arret")


def main_cli() -> int:
    """Mode sans interface : utile pour diagnostiquer."""
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="vox", description="Vox — dictee vocale")
    parser.add_argument("--list-models", action="store_true", help="liste les modeles STT")
    parser.add_argument("--list-devices", action="store_true", help="liste les micros")
    parser.add_argument("--test-key", action="store_true", help="verifie la cle du fournisseur")
    parser.add_argument(
        "--provider",
        choices=["openrouter", "groq", "openai"],
        help="force un fournisseur pour cette commande",
    )
    parser.add_argument("--stats", action="store_true", help="affiche les statistiques d'usage")
    parser.add_argument("--uninstall", action="store_true", help="desinstalle Vox")
    parser.add_argument("--silent", action="store_true", help="sans dialogue de confirmation")
    parser.add_argument("--where", action="store_true", help="affiche les chemins utilises")
    parser.add_argument("--check-update", action="store_true", help="verifie les mises a jour")
    parser.add_argument(
        "--write-manifest",
        metavar="VERSION",
        help="ecrit un gabarit de manifeste de mise a jour",
    )
    parser.add_argument("--set-manifest-url", metavar="URL", help="enregistre l'URL du manifeste")
    parser.add_argument("--transcribe", metavar="FICHIER_WAV", help="transcrit un fichier WAV")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()

    if args.version:
        print(f"Vox {__version__}")
        return 0

    if args.list_devices:
        from .recorder import list_input_devices

        for device in list_input_devices():
            print(f"[{device['index']:>3}] {device['name']}  ({device['hostapi']})")
        return 0

    if args.list_models:
        from . import config as config_module
        from .models import load

        settings = config_module.load()
        catalogue = load(settings.provider, settings.effective_key, force_refresh=True)
        print(f"# Fournisseur : {settings.provider}")
        for model in catalogue.stt:
            per_hour = model.get("per_hour")
            price = f"{per_hour:.3f} $/h" if per_hour else "?"
            print(f"{model['id']:<52} {price:>12}")
        if catalogue.error:
            print(f"# avertissement : {catalogue.error}")
        return 0

    if args.test_key:
        from . import config as config_module
        from .api import Client

        settings = config_module.load()
        key = settings.effective_key
        if not key:
            print(f"Aucune cle trouvee pour {settings.provider} (.env ou reglages).")
            return 1
        try:
            print(json.dumps(Client(settings.provider, key).check_key(), indent=2, ensure_ascii=False))
        except Exception as exc:
            print(f"Echec : {exc}")
            return 1
        return 0

    if args.where:
        from . import install
        from .paths import data_dir

        print(f"programme : {sys.executable}")
        print(f"donnees   : {data_dir()}")
        print(f"installe  : {install.installed_exe()}")
        print(f"demarrage : {'active' if install.is_autostart_enabled() else 'desactive'}")
        return 0

    if args.uninstall:
        from . import install

        return install.uninstall(silent=args.silent)

    if args.write_manifest:
        from .updates import manifest_example

        print(manifest_example(args.write_manifest), end="")
        return 0

    if args.set_manifest_url:
        from . import config as config_module

        current = config_module.load()
        current.update_manifest_url = args.set_manifest_url
        current.check_updates = True
        config_module.save(current)
        print(f"URL du manifeste enregistrée : {args.set_manifest_url}")
        return 0

    if args.check_update:
        from . import config as config_module
        from .updates import check

        current = config_module.load()
        if not current.update_manifest_url:
            print("Aucune URL de manifeste configurée.")
            print("Utilise : vox --set-manifest-url <URL>")
            return 1
        info, reason = check(current.update_manifest_url, __version__)
        if info is None:
            print(f"{reason} (version courante : {__version__})")
            return 0
        print(f"Mise à jour disponible : {info.version} (courante : {__version__})")
        if info.published_at:
            print(f"Publiée le : {info.published_at}")
        if info.notes:
            print(f"Nouveautés : {info.notes}")
        print(f"Téléchargement : {info.url}")
        return 0

    if args.stats:
        from .stats import compute, format_money, summary_lines

        stats = compute()
        if not stats.entries:
            print("Aucune dictée enregistrée pour l'instant.")
            return 0
        rows = summary_lines(stats)
        width = max(len(label) for label, _ in rows)
        for label, value in rows:
            print(f"  {label:<{width}}  {value}")
        if stats.models:
            print("\n  Modèles :")
            for model in stats.models:
                print(
                    f"    {model.model:<44} {model.dictations:>5} dictées  "
                    f"{model.words:>7} mots  {format_money(model.cost)}"
                )
        return 0

    if args.transcribe:
        from . import config as config_module
        from .api import Client

        settings = config_module.load()
        provider = args.provider or settings.provider
        key = config_module.resolve_api_key(settings)
        with open(args.transcribe, "rb") as handle:
            wav = handle.read()
        try:
            result = Client(provider, key).transcribe(
                wav,
                settings.stt_model,
                language=settings.language or None,
                vocabulary=settings.vocabulary_prompt,
            )
        except Exception as exc:
            print(f"Echec : {exc}")
            return 1
        print(result.text)
        print(
            f"\n# modele={result.model} provider={result.provider} "
            f"audio={result.seconds}s latence={result.latency_ms}ms cout={result.cost} $"
        )
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        log.exception("Erreur fatale")
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "Vox", f"Erreur au demarrage :\n{exc}")
        raise SystemExit(1) from exc
