"""Lancement : `python -m vox_relay` (port lu dans PORT, 8080 par défaut)."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from .server import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Le journal d'accès d'aiohttp listerait chaque requête : inutile ici.
    web.run_app(
        create_app(),
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - écoute dans le conteneur
        port=int(os.environ.get("PORT", "8080")),
        access_log=None,
    )


if __name__ == "__main__":
    main()
