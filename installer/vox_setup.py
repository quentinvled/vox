"""Installeur de Vox (tkinter), construit par PyInstaller.

Deux modes :
  * graphique (par defaut) : fenetre d'installation pour tes amis ;
  * `--silent` : aucune interface, tout est journalise dans
    %LOCALAPPDATA%\\Vox\\setup.log. Pratique pour tester ou pour mettre a jour
    en script.

Aucun droit administrateur : tout est installe dans le profil de l'utilisateur.

    uv run python tools/build_installer.py     # construit Vox-Setup-<version>.exe
"""

from __future__ import annotations

import contextlib
import logging
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vox import (
    __version__,
    install,
)
from vox.paths import data_dir

PAYLOAD_FILES = ("Vox.exe", "LISEZ-MOI.txt", ".env.example")
# Facultatif : sans cette icone, le raccourci « Mes enregistrements » n'est pas
# cree (voir install.create_shortcuts).
OPTIONAL_FILES = (install.RECORDINGS_ICON,)
BG = "#14161b"
CARD = "#1f2229"
TEXT = "#f3f5f9"
MUTED = "#949cad"
ACCENT = "#6d9dfb"
STEP_COUNT = 5

log = logging.getLogger("vox.setup")


# ----------------------------------------------------------------------
# Journalisation : sans elle, un echec d'installeur est invisible.
# ----------------------------------------------------------------------
def configure_logging() -> None:
    handlers: list[logging.Handler] = []
    with contextlib.suppress(OSError):
        data_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(data_dir() / "setup.log", encoding="utf-8"))
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s\t%(levelname)s\t%(message)s",
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )


def payload_dir() -> Path:
    """Dossier contenant Vox.exe : ressources PyInstaller ou dist-single."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1] / "dist-single"


def asset(name: str) -> Path | None:
    for candidate in (
        payload_dir() / name,
        Path(__file__).resolve().parents[1] / "assets" / name,
    ):
        if candidate.exists():
            return candidate
    return None


# ----------------------------------------------------------------------
# Coeur de l'installation, partage entre le mode graphique et le mode silencieux
# ----------------------------------------------------------------------
def run_steps(on_step: Callable[[int, str, str], None], launch: bool = True) -> Path:
    """Execute les etapes d'installation.

    `on_step(numero, message, detail)` est appele avant chaque etape. Leve une
    exception en cas d'echec : l'appelant decide comment l'afficher.
    """
    source = payload_dir()
    log.info("Source des fichiers : %s", source)

    missing = [name for name in PAYLOAD_FILES if not (source / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"fichiers introuvables dans l'archive : {', '.join(missing)}"
        )

    on_step(1, "Arrêt de Vox s'il est en cours…", "")
    stopped = install.stop_running()
    log.info("Instances arretees : %s", stopped)

    destination = install.install_dir()
    on_step(2, "Copie des fichiers…", str(destination))
    for name in PAYLOAD_FILES:
        shutil.copy2(source / name, destination / name)
        log.info("Copie : %s", name)
    for name in OPTIONAL_FILES:
        extra = source / name
        if extra.exists():
            shutil.copy2(extra, destination / name)
            log.info("Copie : %s", name)
        else:
            log.warning("Ressource facultative absente : %s", name)
    exe = destination / "Vox.exe"
    if not exe.exists():
        raise FileNotFoundError(f"{exe} absent apres la copie")
    log.info("Vox.exe installe : %s (%s octets)", exe, exe.stat().st_size)

    on_step(3, "Création des raccourcis…", "Bureau et Menu Démarrer")
    shortcuts = install.create_shortcuts(exe, destination / install.RECORDINGS_ICON)
    log.info("Raccourcis crees : %s", shortcuts or "aucun")

    on_step(4, "Enregistrement de la désinstallation…", "Paramètres → Applications")
    install.register_uninstall(__version__, exe)
    log.info("Entree de desinstallation enregistree")

    if launch:
        on_step(5, "Lancement de Vox…", "Recherche de l'icône près de l'horloge")
        subprocess.Popen(  # noqa: S603 - chemin construit localement
            [str(exe)], cwd=str(destination), close_fds=True
        )
        log.info("Vox lance")
    else:
        on_step(5, "Installation terminée.", "Lance Vox depuis le raccourci du Bureau.")

    return exe


def silent_main() -> int:
    """Installation sans interface. Code de sortie 0 si tout s'est bien passe."""
    launch = "--no-launch" not in sys.argv

    def report(step: int, message: str, detail: str) -> None:
        log.info("etape %s/%s : %s %s", step, STEP_COUNT, message, detail)

    try:
        run_steps(report, launch=launch)
    except Exception:
        log.exception("Echec de l'installation")
        return 1
    log.info("Installation silencieuse terminee")
    return 0


# ----------------------------------------------------------------------
# Interface graphique
# ----------------------------------------------------------------------
class SetupApp:
    """Fenetre d'installation."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"Installation de Vox {__version__}")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.events: queue.Queue = queue.Queue()
        self._finished = False

        icon = asset("Vox.ico")
        if icon:
            with contextlib.suppress(tk.TclError):
                self.root.iconbitmap(default=str(icon))

        self._center(560, 360)
        self._build()

    def _center(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 3
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build(self) -> None:
        pad = {"padx": 28}

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", pady=(24, 4), **pad)

        if png := asset("Vox.png"):
            with contextlib.suppress(tk.TclError):
                self._logo = tk.PhotoImage(file=str(png))
                tk.Label(header, image=self._logo, bg=BG).pack(side="left", padx=(0, 14))

        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", anchor="w")
        tk.Label(
            titles, text=f"Vox {__version__}", bg=BG, fg=TEXT,
            font=("Segoe UI", 19, "bold"),
        ).pack(anchor="w")
        tk.Label(
            titles, text="Dictée vocale pour Windows", bg=BG, fg=MUTED, font=("Segoe UI", 10)
        ).pack(anchor="w")

        tk.Label(
            self.root,
            text="Maintenir Ctrl + Maj, parler, relâcher : le texte s'écrit tout seul.",
            bg=BG, fg=TEXT, font=("Segoe UI", 10), wraplength=500, justify="left",
        ).pack(anchor="w", pady=(14, 2), **pad)

        tk.Label(
            self.root,
            text=f"Installation dans : {install.install_dir()}",
            bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=500, justify="left",
        ).pack(anchor="w", pady=(0, 14), **pad)

        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", **pad)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=16, pady=14)

        self.status = tk.Label(
            inner, text="Prêt à installer.", bg=CARD, fg=TEXT, font=("Segoe UI", 10), anchor="w"
        )
        self.status.pack(fill="x")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Vox.Horizontal.TProgressbar",
            troughcolor="#2a2e37", background=ACCENT, bordercolor=CARD,
            lightcolor=ACCENT, darkcolor=ACCENT, thickness=10,
        )
        self.bar = ttk.Progressbar(
            inner, style="Vox.Horizontal.TProgressbar", orient="horizontal",
            length=460, mode="determinate", maximum=STEP_COUNT,
        )
        self.bar.pack(fill="x", pady=(10, 0))

        self.detail = tk.Label(
            self.root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8),
            anchor="w", wraplength=500, justify="left",
        )
        self.detail.pack(fill="x", pady=(10, 0), **pad)

        buttons = tk.Frame(self.root, bg=BG)
        buttons.pack(side="bottom", fill="x", pady=22, **pad)

        self.quit_button = tk.Button(
            buttons, text="Quitter", command=self.root.destroy,
            bg=CARD, fg=TEXT, activebackground="#2a2e37", activeforeground=TEXT,
            font=("Segoe UI", 10), relief="flat", bd=0, padx=18, pady=7, cursor="hand2",
        )
        self.quit_button.pack(side="right")

        self.install_button = tk.Button(
            buttons, text="Installer", command=self.start,
            bg=ACCENT, fg="#ffffff", activebackground="#8fb8ff", activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"), relief="flat", bd=0, padx=22, pady=7, cursor="hand2",
        )
        self.install_button.pack(side="right", padx=(0, 10))

        self.launch_var = tk.BooleanVar(value=True)
        self.launch_check = tk.Checkbutton(
            buttons, text="Lancer Vox à la fin", variable=self.launch_var,
            bg=BG, fg=MUTED, selectcolor=CARD, activebackground=BG,
            activeforeground=TEXT, font=("Segoe UI", 9), bd=0, highlightthickness=0,
        )
        self.launch_check.pack(side="left")

        self.root.bind("<Return>", lambda _e: self.start())
        self.root.bind("<Escape>", lambda _e: self.root.destroy())

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.install_button["state"] == "disabled" or self._finished:
            return
        self.install_button.configure(state="disabled", bg="#39404f")
        self.quit_button.configure(state="disabled")
        self.launch_check.configure(state="disabled")
        self.bar["value"] = 0
        threading.Thread(target=self._work, daemon=True).start()
        self.root.after(80, self._pump)

    def _work(self) -> None:
        def report(step: int, message: str, detail: str) -> None:
            self.events.put(("progress", step, message, detail))

        try:
            run_steps(report, launch=self.launch_var.get())
            self.events.put(("done",))
        except Exception as exc:
            log.exception("Echec de l'installation")
            self.events.put(("error", f"{exc.__class__.__name__} : {exc}"))

    def _pump(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if not self._finished:
            self.root.after(80, self._pump)

    def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "progress":
            _, step, status, detail = event
            self.bar["value"] = step
            self.status.configure(text=status, fg=TEXT)
            self.detail.configure(text=detail, fg=MUTED)
        elif kind == "error":
            self._finished = True
            self.status.configure(text="Échec de l'installation", fg="#f87171")
            self.detail.configure(
                text=f"{event[1]}\nDétail complet dans {data_dir() / 'setup.log'}", fg="#f87171"
            )
            self.install_button.configure(state="normal", bg=ACCENT, text="Réessayer")
            self.install_button.configure(command=self._retry)
            self.quit_button.configure(state="normal")
        elif kind == "done":
            self._finished = True
            self.bar["value"] = STEP_COUNT
            self.status.configure(text="Vox est installé.", fg="#4ade80")
            self.detail.configure(
                text="Clic droit sur l'icône de la zone de notification pour le menu. "
                "Au premier lancement, saisis ta clé OpenRouter dans les réglages."
            )
            self.install_button.configure(state="normal", bg=ACCENT, text="Terminer")
            self.install_button.configure(command=self.root.destroy)
            self.quit_button.configure(state="normal", text="Fermer")

    def _retry(self) -> None:
        self._finished = False
        self.install_button.configure(command=self.start)
        self.status.configure(text="Prêt à installer.", fg=TEXT)
        self.detail.configure(text="")
        self.start()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    configure_logging()
    if sys.platform != "win32":
        log.error("Cet installeur ne fonctionne que sous Windows.")
        return 1

    if "--silent" in sys.argv:
        return silent_main()

    log.info("Installeur Vox %s demarre (mode graphique)", __version__)
    return SetupApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
