"""Dictee depuis le telephone, a travers le relais vox.vled.fr.

    telephone (page web)  ⇄  relais (dossier relay/)  ⇄  PhoneLink  →  Pipeline

Le relais ne fait que transmettre. Cet ordinateur s'y presente avec un secret
(`Settings.phone_secret`), dont est derive l'identifiant de son « salon », et
la liste des empreintes des telephones autorises (`Settings.phones`). Le code
QR d'appairage contient l'identifiant du salon et un jeton propre au
telephone ; seule l'empreinte de ce jeton est conservee ici.

L'audio arrive en PCM 16 bits, 16 kHz, mono, dans `recorder` (un
`StreamRecorder`) : le pipeline le traite comme une dictee au micro, et le
texte s'insere dans la fenetre active de l'ordinateur.

Protocole complet : relay/README.md.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import logging
import secrets
import sys
import time
from datetime import datetime
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from . import __version__
from .config import DEFAULT_PHONE_RELAY, Settings
from .recorder import StreamRecorder

log = logging.getLogger(__name__)

# Codes de fermeture du relais (voir relay/vox_relay/server.py).
CLOSE_BAD_AUTH = 4001
CLOSE_REPLACED = 4009

RETRY_DELAYS = (1, 2, 4, 8, 15, 30)  # secondes
REPLACED_RETRY = 60
WATCHDOG_MS = 15000
SILENCE_LIMIT = 50.0  # sans nouvelles du relais au-dela : connexion morte


# ----------------------------------------------------------------------
# Identifiants (memes calculs que relay/vox_relay/rooms.py)
# ----------------------------------------------------------------------
def new_secret() -> str:
    return secrets.token_urlsafe(32)


def room_for_secret(secret: str) -> str:
    digest = hashlib.sha256(b"vox-room:" + secret.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")[:22]


def fingerprint(token: str) -> str:
    return hashlib.sha256(b"vox-phone:" + token.encode("ascii")).hexdigest()[:32]


def pairing_page(relay_url: str) -> str:
    """Page du telephone servie par le relais : wss://hote/ws -> https://hote/."""
    parts = urlsplit(relay_url or DEFAULT_PHONE_RELAY)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}/"


def computer_name() -> str:
    """Nom montre sur le telephone (« PC de Quentin », « Mac de Quentin »)."""
    try:
        user = getpass.getuser().strip()
    except Exception:
        user = ""
    device = "Mac" if sys.platform == "darwin" else "PC"
    if not user or user.lower() in {"user", "admin", "administrator", "utilisateur"}:
        return f"Mon {device}"
    return f"{device} de {user[:1].upper()}{user[1:]}"


class PhoneLink(QObject):
    """Connexion permanente au relais, tant qu'au moins un telephone est relie."""

    state_changed = Signal(str)  # off | connecting | online | waiting
    phones_changed = Signal()  # liste des telephones ou leur presence
    paired = Signal(str)  # nom du telephone qui vient d'etre relie
    settings_changed = Signal()  # secret ou liste modifies : a enregistrer

    capture_started = Signal(str)  # mode : dictation | instruction
    capture_stopped = Signal()
    capture_cancelled = Signal()
    mode_requested = Signal(str)
    action_requested = Signal(str)  # enter | undo | reinsert

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.recorder = StreamRecorder(max_seconds=settings.max_record_seconds)
        self.name = computer_name()

        self._socket: QWebSocket | None = None
        self._state = "off"
        self._ready = False
        self._attempt = 0
        self._last_seen = 0.0
        self._online: dict[str, str] = {}  # empreinte -> nom
        self._pending: set[str] = set()  # empreintes en cours d'appairage
        self._speaker = ""  # empreinte du telephone qui parle

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._connect)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_MS)
        self._watchdog.timeout.connect(self._check_alive)

    # ------------------------------------------------------------------
    # Etat
    # ------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def phones(self) -> list[dict]:
        phones = self.settings.phones
        return [p for p in phones if isinstance(p, dict) and p.get("id")] if isinstance(phones, list) else []

    def is_online(self, phone_id: str) -> bool:
        return phone_id in self._online

    @property
    def wanted(self) -> bool:
        """Une connexion n'a de sens qu'avec un telephone relie (ou en cours)."""
        return bool(self.phones or self._pending)

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.wanted:
            self._connect()

    def apply_settings(self, settings: Settings) -> None:
        previous = self.settings.phone_relay_url
        self.settings = settings
        self.recorder.max_seconds = settings.max_record_seconds
        if settings.phone_relay_url != previous and self._socket is not None:
            self._drop()
            self.start()

    def close(self) -> None:
        self._retry.stop()
        self._drop()
        self._set_state("off")

    def reconnect(self) -> None:
        """Relance seulement la liaison, sans modifier les téléphones reliés."""
        self._retry.stop()
        self._attempt = 0
        self._drop()
        self._connect()

    def _drop(self) -> None:
        self._watchdog.stop()
        socket, self._socket = self._socket, None
        self._ready = False
        if socket is not None:
            socket.disconnected.disconnect()
            socket.close()
            socket.deleteLater()
        self._lost_phones()

    def _connect(self) -> None:
        if not self.wanted:
            self.close()
            return
        if self._socket is not None:
            return
        if not self.settings.phone_secret:
            self.settings.phone_secret = new_secret()
            self.settings_changed.emit()
        socket = QWebSocket(parent=self)
        socket.connected.connect(self._on_connected)
        socket.disconnected.connect(self._on_disconnected)
        socket.errorOccurred.connect(self._on_error)
        socket.textMessageReceived.connect(self._on_text)
        socket.binaryMessageReceived.connect(self._on_binary)
        socket.pong.connect(self._touch)
        self._socket = socket
        self._set_state("connecting")
        self._touch()
        self._watchdog.start()
        socket.open(QUrl(self.settings.phone_relay_url or DEFAULT_PHONE_RELAY))

    def _on_connected(self) -> None:
        self._touch()
        self._send_raw(
            {
                "t": "auth",
                "role": "computer",
                "secret": self.settings.phone_secret,
                "allowed": self._allowlist(),
                "name": self.name,
                "version": __version__,
            }
        )

    def _on_error(self, _error) -> None:
        socket = self._socket
        if socket is None:
            return
        log.info("Relais injoignable : %s", socket.errorString())
        # Un echec de connexion n'emet pas toujours `disconnected` : on conclut
        # nous-memes, une fois la pile Qt revenue au calme.
        QTimer.singleShot(0, lambda: self._settle_failure(socket))

    def _settle_failure(self, socket: QWebSocket) -> None:
        if self._socket is socket and socket.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._on_disconnected()

    def _on_disconnected(self) -> None:
        socket = self._socket
        code = int(socket.closeCode()) if socket is not None else 0
        reason = socket.closeReason() if socket is not None else ""
        self._drop()
        if not self.wanted:
            self._set_state("off")
            return
        if code == CLOSE_BAD_AUTH:
            log.warning("Relais : authentification refusée (%s)", reason)
            delay = REPLACED_RETRY
        elif code == CLOSE_REPLACED:
            # Un autre Vox utilise le meme secret (reglages copies) : on ne se
            # dispute pas la place en boucle.
            log.warning("Relais : un autre ordinateur a pris la place de celui-ci")
            delay = REPLACED_RETRY
        else:
            delay = RETRY_DELAYS[min(self._attempt, len(RETRY_DELAYS) - 1)]
            self._attempt += 1
        self._set_state("waiting")
        self._retry.start(delay * 1000)

    def _touch(self, *_args) -> None:
        self._last_seen = time.monotonic()

    def _check_alive(self) -> None:
        """Detecte une connexion morte (veille, changement de reseau)."""
        socket = self._socket
        if socket is None:
            return
        if time.monotonic() - self._last_seen > SILENCE_LIMIT:
            log.info("Relais muet : reconnexion")
            socket.abort()
            if self._socket is socket:
                self._on_disconnected()
            return
        if socket.state() == QAbstractSocket.SocketState.ConnectedState:
            socket.ping()

    # ------------------------------------------------------------------
    # Appairage
    # ------------------------------------------------------------------
    def begin_pairing(self) -> str:
        """Prepare un nouveau telephone et renvoie l'adresse a encoder en QR."""
        if not self.settings.phone_secret:
            self.settings.phone_secret = new_secret()
            self.settings_changed.emit()
        token = secrets.token_urlsafe(32)
        self._pending.add(fingerprint(token))
        self._push_allowlist()
        room = room_for_secret(self.settings.phone_secret)
        return f"{pairing_page(self.settings.phone_relay_url)}#{room}.{token}"

    def end_pairing(self) -> None:
        """Le code n'est plus affiche : les jetons non utilises sont oublies."""
        if self._pending:
            self._pending.clear()
            self._push_allowlist()

    def remove_phone(self, phone_id: str) -> None:
        self.settings.phones = [p for p in self.phones if p.get("id") != phone_id]
        self._online.pop(phone_id, None)
        self.settings_changed.emit()
        self.phones_changed.emit()
        self._push_allowlist()

    def _allowlist(self) -> list[str]:
        return [p["id"] for p in self.phones] + sorted(self._pending)

    def _push_allowlist(self) -> None:
        if not self.wanted:
            self.close()
        elif self._ready:
            self._send({"t": "allow", "allowed": self._allowlist()})
        elif self._socket is None:
            self._retry.stop()
            self._attempt = 0
            self._connect()

    def _register(self, phone_id: str, name: str) -> None:
        """Premier contact d'un telephone en cours d'appairage."""
        self._pending.discard(phone_id)
        phones = [p for p in self.phones if p.get("id") != phone_id]
        phones.append(
            {"id": phone_id, "name": name, "added": datetime.now().isoformat(timespec="seconds")}
        )
        self.settings.phones = phones
        self.settings_changed.emit()
        self.paired.emit(name)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def send_state(self, state: str, text: str = "", message: str = "") -> None:
        payload: dict = {"t": "state", "state": state}
        if text:
            payload["text"] = text
        if message:
            payload["message"] = message
        self._send(payload)

    def send_notice(self, message: str) -> None:
        self._send({"t": "notice", "message": message})

    def _send(self, payload: dict) -> None:
        """Envoi apres authentification (avant, le relais attend `auth`)."""
        if self._ready:
            self._send_raw(payload)

    def _send_raw(self, payload: dict) -> None:
        if self._socket is not None:
            self._socket.sendTextMessage(json.dumps(payload, ensure_ascii=False))

    def _on_binary(self, data) -> None:
        self._touch()
        if self._speaker:
            self.recorder.feed(bytes(data))

    def _on_text(self, text: str) -> None:
        self._touch()
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("t")
        sender = str(message.get("from", ""))

        if kind == "ready":
            self._ready = True
            self._attempt = 0
            self._send({"t": "allow", "allowed": self._allowlist()})
            self._set_state("online")
        elif kind == "phone":
            self._on_presence(
                str(message.get("id", "")), str(message.get("name", "")), bool(message.get("online"))
            )
        elif kind == "start":
            self._speaker = sender
            mode = "instruction" if message.get("mode") == "instruction" else "dictation"
            self.capture_started.emit(mode)
        elif kind == "mode" and sender == self._speaker:
            self.mode_requested.emit("instruction" if message.get("mode") == "instruction" else "dictation")
        elif kind == "stop" and sender == self._speaker:
            self._speaker = ""
            self.capture_stopped.emit()
        elif kind == "cancel" and sender == self._speaker:
            self._speaker = ""
            self.capture_cancelled.emit()
        elif kind == "action" and message.get("name") in {"enter", "undo", "reinsert"}:
            self.action_requested.emit(message["name"])
        elif kind == "unpair" and sender:
            self.remove_phone(sender)

    def _on_presence(self, phone_id: str, name: str, online: bool) -> None:
        if not phone_id:
            return
        if online:
            self._online[phone_id] = name
            if phone_id in self._pending:
                self._register(phone_id, name)
            else:
                for phone in self.phones:
                    if phone["id"] == phone_id and name and phone.get("name") != name:
                        phone["name"] = name
                        self.settings_changed.emit()
        else:
            self._online.pop(phone_id, None)
            if phone_id == self._speaker:
                self._speaker = ""
                self.capture_cancelled.emit()
        self.phones_changed.emit()

    def _lost_phones(self) -> None:
        if self._speaker:
            self._speaker = ""
            self.capture_cancelled.emit()
        if self._online:
            self._online.clear()
            self.phones_changed.emit()


__all__ = ["PhoneLink", "computer_name", "fingerprint", "pairing_page", "room_for_secret"]
