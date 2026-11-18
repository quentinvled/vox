"""Serveur du relais : page du téléphone et canal WebSocket `/ws`.

Le relais ne comprend pas l'audio ni les consignes : il authentifie, puis
transmet. Les messages texte sont du JSON avec un champ `t` (type) ; l'audio
voyage en messages binaires (PCM 16 bits, 16 kHz, mono), uniquement du
téléphone qui parle vers l'ordinateur.

Protocole complet : relay/README.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from aiohttp import WSCloseCode, WSMsgType, web

from . import __version__
from .rooms import (
    MAX_PHONES_PER_ROOM,
    Peer,
    Registry,
    Room,
    clean_allowlist,
    clean_name,
    fingerprint,
    room_for_secret,
    valid_room,
    valid_token,
)

log = logging.getLogger("vox_relay")

AUTH_TIMEOUT = 10.0
HEARTBEAT = 25.0  # Cloudflare coupe une connexion muette au bout de 100 s.
MAX_MESSAGE = 256 * 1024

# Codes de fermeture (plage 4000-4999 réservée aux applications).
CLOSE_BAD_AUTH = 4001
CLOSE_NOT_ALLOWED = 4003
CLOSE_ROOM_FULL = 4008
CLOSE_REPLACED = 4009
CLOSE_RELAY_FULL = 4013

# Types que le relais traite lui-même et ne transmet jamais.
_RESERVED = {"auth", "allow", "ready", "computer", "phone"}

REGISTRY = web.AppKey("registry", Registry)
WEB_ROOT = web.AppKey("web_root", Path)


# ----------------------------------------------------------------------
# Envoi
# ----------------------------------------------------------------------
async def _send(peer: Peer | None, payload: dict[str, Any]) -> None:
    if peer is None or peer.socket.closed:
        return
    # Une erreur d'envoi signifie une connexion qui se ferme : sa propre boucle
    # s'en occupe.
    with contextlib.suppress(ConnectionError, RuntimeError):
        await peer.socket.send_str(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


async def _send_bytes(peer: Peer | None, data: bytes) -> None:
    if peer is None or peer.socket.closed:
        return
    with contextlib.suppress(ConnectionError, RuntimeError):
        await peer.socket.send_bytes(data)


async def _close(peer: Peer, code: int, reason: str) -> None:
    if not peer.socket.closed:
        await peer.socket.close(code=code, message=reason.encode())


def _phone_info(phone: Peer) -> dict[str, str]:
    return {"id": phone.fingerprint, "name": phone.name}


def _computer_state(room: Room) -> dict[str, Any]:
    computer = room.computer
    return {"online": computer is not None, "name": computer.name if computer else ""}


# ----------------------------------------------------------------------
# Ordinateur
# ----------------------------------------------------------------------
async def _apply_allowlist(room: Room) -> None:
    accepted, refused = room.review()
    for phone in refused:
        await _close(phone, CLOSE_NOT_ALLOWED, "phone not allowed")
    for phone in accepted:
        await _send(phone, {"t": "computer", **_computer_state(room)})
        await _send(room.computer, {"t": "phone", "online": True, **_phone_info(phone)})


async def _serve_computer(ws: web.WebSocketResponse, registry: Registry, hello: dict) -> None:
    secret = hello.get("secret")
    if not valid_token(secret):
        await ws.close(code=CLOSE_BAD_AUTH, message=b"bad secret")
        return
    room = registry.open(room_for_secret(secret))
    if room is None:
        await ws.close(code=CLOSE_RELAY_FULL, message=b"relay full")
        return

    previous = room.computer
    me = Peer(ws, clean_name(hello.get("name"), "Ordinateur"))
    room.computer = me
    room.allowed = clean_allowlist(hello.get("allowed"))
    if previous is not None:
        await _close(previous, CLOSE_REPLACED, "replaced")
    log.info("ordinateur connecté (salon %s…)", room.id[:6])

    await _send(me, {"t": "ready", "room": room.id})
    # Chaque téléphone présent est rejugé face à ce nouvel ordinateur : les
    # autorisés le voient arriver, l'ordinateur les voit en ligne.
    for phone in room.phones:
        phone.authorized = False
    await _apply_allowlist(room)

    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            payload = _parse(message.data)
            if payload is None:
                continue
            kind = payload.get("t")
            if kind == "allow":
                room.allowed = clean_allowlist(payload.get("allowed"))
                await _apply_allowlist(room)
            elif kind not in _RESERVED:
                target = payload.pop("to", None)
                for phone in room.online_phones():
                    if target is None or phone.fingerprint == target:
                        await _send(phone, payload)
    finally:
        if room.computer is me:
            room.computer = None
            room.speaker = None
            for phone in room.online_phones():
                phone.authorized = False
                await _send(phone, {"t": "computer", "online": False, "name": me.name})
        registry.release(room)
        log.info("ordinateur déconnecté (salon %s…)", room.id[:6])


# ----------------------------------------------------------------------
# Téléphone
# ----------------------------------------------------------------------
async def _serve_phone(ws: web.WebSocketResponse, registry: Registry, hello: dict) -> None:
    room_id, token = hello.get("room"), hello.get("token")
    if not valid_room(room_id) or not valid_token(token):
        await ws.close(code=CLOSE_BAD_AUTH, message=b"bad pairing")
        return
    room = registry.open(room_id)
    if room is None:
        await ws.close(code=CLOSE_RELAY_FULL, message=b"relay full")
        return
    if len(room.phones) >= MAX_PHONES_PER_ROOM:
        registry.release(room)
        await ws.close(code=CLOSE_ROOM_FULL, message=b"room full")
        return

    me = Peer(ws, clean_name(hello.get("name"), "Téléphone"), fingerprint(token))
    if room.computer is not None and me.fingerprint not in room.allowed:
        registry.release(room)
        await ws.close(code=CLOSE_NOT_ALLOWED, message=b"phone not allowed")
        return
    me.authorized = room.computer is not None
    room.phones.append(me)

    await _send(me, {"t": "ready", "id": me.fingerprint, "computer": _computer_state(room)})
    if me.authorized:
        await _send(room.computer, {"t": "phone", "online": True, **_phone_info(me)})

    try:
        async for message in ws:
            if not me.authorized or room.computer is None:
                continue  # En attente de l'ordinateur : rien n'est relayé.
            if message.type == WSMsgType.BINARY:
                if room.speaker is me:
                    await _send_bytes(room.computer, message.data)
                continue
            if message.type != WSMsgType.TEXT:
                continue
            payload = _parse(message.data)
            if payload is None or payload.get("t") in _RESERVED:
                continue
            kind = payload["t"]
            if kind == "start":
                room.speaker = me
            elif kind in {"stop", "cancel"} and room.speaker is not me:
                continue  # Seul le téléphone qui parle peut conclure.
            payload["from"] = me.fingerprint
            await _send(room.computer, payload)
            if kind in {"stop", "cancel"}:
                room.speaker = None
    finally:
        if me in room.phones:
            room.phones.remove(me)
        if room.speaker is me:
            room.speaker = None
            await _send(room.computer, {"t": "cancel", "from": me.fingerprint})
        if me.authorized:
            await _send(room.computer, {"t": "phone", "online": False, **_phone_info(me)})
        registry.release(room)


# ----------------------------------------------------------------------
# Point d'entrée WebSocket
# ----------------------------------------------------------------------
def _parse(data: str) -> dict | None:
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("t"), str):
        return None
    return payload


async def websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT, max_msg_size=MAX_MESSAGE, compress=False)
    await ws.prepare(request)
    registry = request.app[REGISTRY]
    try:
        first = await asyncio.wait_for(ws.receive(), AUTH_TIMEOUT)
    except TimeoutError:
        await ws.close(code=CLOSE_BAD_AUTH, message=b"auth timeout")
        return ws
    hello = _parse(first.data) if first.type == WSMsgType.TEXT else None
    if hello is None or hello.get("t") != "auth":
        await ws.close(code=CLOSE_BAD_AUTH, message=b"auth expected")
        return ws

    role = hello.get("role")
    if role == "computer":
        await _serve_computer(ws, registry, hello)
    elif role == "phone":
        await _serve_phone(ws, registry, hello)
    else:
        await ws.close(code=CLOSE_BAD_AUTH, message=b"unknown role")
    if not ws.closed:
        await ws.close(code=WSCloseCode.GOING_AWAY)
    return ws


# ----------------------------------------------------------------------
# Page du téléphone
# ----------------------------------------------------------------------
async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": __version__, **request.app[REGISTRY].stats()})


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(request.app[WEB_ROOT] / "index.html")


@web.middleware
async def headers(request: web.Request, handler):
    response = await handler(request)
    if isinstance(response, web.WebSocketResponse):
        return response
    host = request.host
    response.headers.setdefault("Cache-Control", "no-cache")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"connect-src 'self' wss://{host} ws://{host}; "
        "img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "worker-src 'self'; manifest-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    return response


def create_app(web_root: Path | None = None) -> web.Application:
    default = Path(__file__).resolve().parent.parent / "web"
    root = web_root or Path(os.environ.get("VOX_RELAY_WEB", default))
    app = web.Application(middlewares=[headers])
    app[REGISTRY] = Registry()
    app[WEB_ROOT] = root
    app.router.add_get("/ws", websocket)
    app.router.add_get("/healthz", health)
    app.router.add_get("/", index)
    app.router.add_static("/", root, show_index=False, follow_symlinks=False)
    return app
