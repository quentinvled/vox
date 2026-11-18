"""Salons du relais : un ordinateur et les téléphones qu'il autorise.

Le relais ne garde rien sur disque. Un salon existe tant qu'un ordinateur ou
un téléphone y est connecté :

* l'ordinateur se présente avec son secret ; l'identifiant du salon en est
  dérivé, si bien que seul le détenteur du secret peut occuper la place de
  l'ordinateur ;
* il annonce la liste des empreintes de téléphones autorisés ;
* un téléphone se présente avec l'identifiant du salon et son jeton ; il n'est
  relayé que si l'empreinte de son jeton figure dans la liste.

Un téléphone qui arrive pendant que l'ordinateur est absent attend, sans rien
pouvoir envoyer. Il est accepté ou renvoyé dès que l'ordinateur revient.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# Limites : de quoi servir une personne et quelques appareils, pas davantage.
MAX_ROOMS = 2000
MAX_PHONES_PER_ROOM = 8
MAX_ALLOWED_PHONES = 32
MAX_NAME_LENGTH = 40

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_ROOM = re.compile(r"^[A-Za-z0-9_-]{22}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{32}$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def room_for_secret(secret: str) -> str:
    """Identifiant public du salon d'un ordinateur (22 caractères, 128 bits)."""
    return _b64(hashlib.sha256(b"vox-room:" + secret.encode("ascii")).digest())[:22]


def fingerprint(token: str) -> str:
    """Empreinte d'un jeton de téléphone : c'est elle que l'ordinateur autorise."""
    return hashlib.sha256(b"vox-phone:" + token.encode("ascii")).hexdigest()[:32]


def valid_token(value: Any) -> bool:
    return isinstance(value, str) and bool(_TOKEN.match(value))


def valid_room(value: Any) -> bool:
    return isinstance(value, str) and bool(_ROOM.match(value))


def clean_allowlist(values: Any) -> frozenset[str]:
    """Garde les empreintes bien formées, sans dépasser la limite."""
    if not isinstance(values, list):
        return frozenset()
    kept = [v for v in values if isinstance(v, str) and _FINGERPRINT.match(v)]
    return frozenset(kept[:MAX_ALLOWED_PHONES])


def clean_name(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    name = " ".join(value.split())[:MAX_NAME_LENGTH]
    return name or fallback


@dataclass(eq=False)
class Peer:
    """Une connexion : `socket` est l'objet WebSocket (opaque ici)."""

    socket: Any
    name: str
    fingerprint: str = ""
    authorized: bool = False


@dataclass(eq=False)
class Room:
    id: str
    computer: Peer | None = None
    allowed: frozenset[str] = frozenset()
    phones: list[Peer] = field(default_factory=list)
    # Téléphone dont l'audio est en cours de transmission.
    speaker: Peer | None = None

    @property
    def empty(self) -> bool:
        return self.computer is None and not self.phones

    def online_phones(self) -> list[Peer]:
        return [phone for phone in self.phones if phone.authorized]

    def review(self) -> tuple[list[Peer], list[Peer]]:
        """Réévalue les téléphones après un changement de la liste autorisée.

        Renvoie (nouvellement acceptés, à renvoyer). Sans ordinateur, personne
        n'est jugé : les téléphones attendent.
        """
        if self.computer is None:
            return [], []
        accepted, refused = [], []
        for phone in self.phones:
            allowed = phone.fingerprint in self.allowed
            if allowed and not phone.authorized:
                phone.authorized = True
                accepted.append(phone)
            elif not allowed:
                refused.append(phone)
        return accepted, refused


class Registry:
    """Tous les salons ouverts, en mémoire."""

    def __init__(self, max_rooms: int = MAX_ROOMS) -> None:
        self.max_rooms = max_rooms
        self._rooms: dict[str, Room] = {}

    def __len__(self) -> int:
        return len(self._rooms)

    def get(self, room_id: str) -> Room | None:
        return self._rooms.get(room_id)

    def open(self, room_id: str) -> Room | None:
        """Salon existant ou nouveau ; None si le relais est plein."""
        room = self._rooms.get(room_id)
        if room is None:
            if len(self._rooms) >= self.max_rooms:
                return None
            room = self._rooms[room_id] = Room(room_id)
        return room

    def release(self, room: Room) -> None:
        if room.empty and self._rooms.get(room.id) is room:
            del self._rooms[room.id]

    def stats(self) -> dict[str, int]:
        rooms = list(self._rooms.values())
        return {
            "rooms": len(rooms),
            "computers": sum(1 for room in rooms if room.computer is not None),
            "phones": sum(len(room.phones) for room in rooms),
        }
