"""Tests du relais : appairage, autorisation, transmission et révocation."""

from __future__ import annotations

import json
import secrets

import pytest
from aiohttp import WSMsgType

from vox_relay.rooms import Registry, fingerprint, room_for_secret
from vox_relay.server import CLOSE_NOT_ALLOWED, CLOSE_REPLACED, create_app


def _token() -> str:
    return secrets.token_urlsafe(32)


@pytest.fixture
async def client(aiohttp_client, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Vox</title>")
    return await aiohttp_client(create_app(tmp_path))


async def _json(ws) -> dict:
    message = await ws.receive(timeout=2)
    assert message.type == WSMsgType.TEXT, message
    return json.loads(message.data)


async def _computer(client, secret: str, allowed: list[str]):
    ws = await client.ws_connect("/ws")
    await ws.send_json(
        {"t": "auth", "role": "computer", "secret": secret, "allowed": allowed, "name": "PC"}
    )
    ready = await _json(ws)
    assert ready["t"] == "ready"
    assert ready["room"] == room_for_secret(secret)
    return ws


async def _phone(client, room: str, token: str):
    ws = await client.ws_connect("/ws")
    await ws.send_json({"t": "auth", "role": "phone", "room": room, "token": token, "name": "iPhone"})
    return ws


async def test_paired_phone_reaches_computer(client) -> None:
    secret, token = _token(), _token()
    computer = await _computer(client, secret, [fingerprint(token)])
    phone = await _phone(client, room_for_secret(secret), token)

    ready = await _json(phone)
    assert ready == {
        "t": "ready",
        "id": fingerprint(token),
        "computer": {"online": True, "name": "PC"},
    }
    assert await _json(computer) == {
        "t": "phone",
        "online": True,
        "id": fingerprint(token),
        "name": "iPhone",
    }

    await phone.send_json({"t": "start", "mode": "instruction"})
    await phone.send_bytes(b"\x01\x02" * 800)
    await phone.send_json({"t": "stop"})
    start = await _json(computer)
    assert start == {"t": "start", "mode": "instruction", "from": fingerprint(token)}
    audio = await computer.receive(timeout=2)
    assert audio.type == WSMsgType.BINARY
    assert audio.data == b"\x01\x02" * 800
    assert (await _json(computer))["t"] == "stop"

    await computer.send_json({"t": "state", "state": "done", "text": "Bonjour"})
    assert await _json(phone) == {"t": "state", "state": "done", "text": "Bonjour"}


async def test_unknown_phone_is_refused(client) -> None:
    secret = _token()
    await _computer(client, secret, [fingerprint(_token())])
    phone = await _phone(client, room_for_secret(secret), _token())
    message = await phone.receive(timeout=2)
    assert message.type == WSMsgType.CLOSE
    assert message.data == CLOSE_NOT_ALLOWED


async def test_phone_waits_for_computer_then_is_accepted(client) -> None:
    secret, token = _token(), _token()
    room = room_for_secret(secret)
    phone = await _phone(client, room, token)
    ready = await _json(phone)
    assert ready["computer"] == {"online": False, "name": ""}

    # Tant que l'ordinateur est absent, rien n'est relayé.
    await phone.send_json({"t": "start", "mode": "dictation"})

    computer = await _computer(client, secret, [fingerprint(token)])
    assert (await _json(phone)) == {"t": "computer", "online": True, "name": "PC"}
    assert (await _json(computer))["t"] == "phone"

    await computer.close()
    assert (await _json(phone)) == {"t": "computer", "online": False, "name": "PC"}


async def test_revocation_closes_the_phone(client) -> None:
    secret, token = _token(), _token()
    computer = await _computer(client, secret, [fingerprint(token)])
    phone = await _phone(client, room_for_secret(secret), token)
    await _json(phone)
    await _json(computer)

    await computer.send_json({"t": "allow", "allowed": []})
    message = await phone.receive(timeout=2)
    assert message.type == WSMsgType.CLOSE
    assert message.data == CLOSE_NOT_ALLOWED


async def test_only_the_speaking_phone_streams_audio(client) -> None:
    secret, first, second = _token(), _token(), _token()
    computer = await _computer(client, secret, [fingerprint(first), fingerprint(second)])
    room = room_for_secret(secret)
    a = await _phone(client, room, first)
    b = await _phone(client, room, second)
    for ws in (a, b):
        await _json(ws)
    await _json(computer)
    await _json(computer)

    await a.send_json({"t": "start", "mode": "dictation"})
    await b.send_bytes(b"intrus")
    await b.send_json({"t": "stop"})
    await a.send_bytes(b"voix")
    assert (await _json(computer))["from"] == fingerprint(first)
    audio = await computer.receive(timeout=2)
    assert audio.data == b"voix"

    # Le téléphone qui parle se déconnecte : l'ordinateur annule la prise.
    await a.close()
    assert await _json(computer) == {"t": "cancel", "from": fingerprint(first)}


async def test_new_computer_replaces_the_old_one(client) -> None:
    secret = _token()
    old = await _computer(client, secret, [])
    await _computer(client, secret, [])
    message = await old.receive(timeout=2)
    assert message.type == WSMsgType.CLOSE
    assert message.data == CLOSE_REPLACED


async def test_bad_auth_and_static_page(client) -> None:
    ws = await client.ws_connect("/ws")
    await ws.send_json({"t": "auth", "role": "computer", "secret": "court"})
    message = await ws.receive(timeout=2)
    assert message.type == WSMsgType.CLOSE

    page = await client.get("/")
    assert page.status == 200
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    health = await client.get("/healthz")
    assert (await health.json())["ok"] is True


def test_registry_releases_empty_rooms() -> None:
    registry = Registry(max_rooms=1)
    room = registry.open("a" * 22)
    assert room is not None
    assert registry.open("b" * 22) is None
    registry.release(room)
    assert len(registry) == 0


async def test_ping_is_answered_even_while_waiting(client) -> None:
    phone = await _phone(client, room_for_secret(_token()), _token())
    await _json(phone)
    await phone.send_json({"t": "ping"})
    assert await _json(phone) == {"t": "pong"}
