"""Point d'entree pour PyInstaller.

`src/vox/__main__.py` utilise des imports relatifs (`from . import ...`) : il ne
peut donc pas servir de script d'entree a PyInstaller, qui l'executerait comme
`__main__` et non comme `vox.__main__`. Ce petit lanceur conserve le paquet
intact et se contente d'appeler `main()`.
"""

from __future__ import annotations

import multiprocessing
import sys

if __name__ == "__main__":
    # Obligatoire pour PyInstaller sur Windows : evite de relancer l'app
    # entiere si un module utilise multiprocessing.
    multiprocessing.freeze_support()
    from vox.__main__ import main

    sys.exit(main())
