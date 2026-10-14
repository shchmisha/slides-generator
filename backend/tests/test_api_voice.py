"""Voice settings: the user's ElevenLabs key is checked, encrypted, and never shown again."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import VoiceSettings
from app.voice import elevenlabs

from .api_support import signed_up
from .fake_elevenlabs import KEY, FakeElevenLabs


@pytest.fixture
def eleven(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeElevenLabs]:
    fake = FakeElevenLabs()
    monkeypatch.setattr(elevenlabs, "transport", fake.transport())
    yield fake


def test_without_a_key_voice_is_off(app: Any) -> None:
    client = signed_up(app)
    assert client.get("/api/voice").json() == {
        "enabled": False,
        "voice_id": None,
        "voice_name": None,
        "sounds": True,
        "updated_at": None,
        "model": "eleven_v4",
    }
    assert client.get("/api/voice/voices").status_code == 409


def test_key_is_checked_stored_encrypted_and_never_returned(
    app: Any,
    eleven: FakeElevenLabs,
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    client = signed_up(app)
    resp = client.put("/api/voice", json={"api_key": KEY})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] and resp.json()["voice_name"] == "George"  # the default voice
    answers = [resp.text, client.get("/api/voice").text, client.get("/api/voice/voices").text]
    assert not any(KEY in text or KEY[-6:] in text for text in answers)
    assert KEY not in caplog.text
    with session_factory() as db:
        row = db.scalars(select(VoiceSettings)).one()
        assert KEY.encode() not in row.api_key
    # Calls to ElevenLabs carried the key in the header only.
    assert all(KEY not in path for _, path, _ in eleven.calls)


def test_a_wrong_key_is_refused_without_echo(app: Any, eleven: FakeElevenLabs) -> None:
    client = signed_up(app)
    wrong = "sk_wrong_" + "x" * 30
    resp = client.put("/api/voice", json={"api_key": wrong})
    assert resp.status_code == 422 and "не подходит" in resp.json()["detail"]
    assert wrong not in resp.text
    assert client.get("/api/voice").json()["enabled"] is False
    for bad in ("short", "sk_with space " + "x" * 20, "sk_\u0000" + "x" * 20):
        resp = client.put("/api/voice", json={"api_key": bad})
        assert resp.status_code == 422 and bad not in resp.text


def test_voice_can_be_chosen_from_the_accounts_voices(app: Any, eleven: FakeElevenLabs) -> None:
    client = signed_up(app)
    client.put("/api/voice", json={"api_key": KEY})
    names = [v["name"] for v in client.get("/api/voice/voices").json()]
    assert names == ["George", "Sarah"]
    resp = client.patch("/api/voice", json={"voice_id": "EXAVITQu4vr4xnSDxMaL"})
    assert resp.status_code == 200 and resp.json()["voice_name"] == "Sarah"
    assert client.patch("/api/voice", json={"voice_id": "nope123"}).status_code == 422
    assert client.patch("/api/voice", json={"voice_id": "../x"}).status_code == 422
    resp = client.patch("/api/voice", json={"sounds": False})
    assert resp.json()["sounds"] is False and resp.json()["voice_name"] == "Sarah"
    # Replacing the key keeps the chosen voice.
    assert client.put("/api/voice", json={"api_key": KEY}).json()["voice_name"] == "Sarah"


def test_keys_are_per_user_and_can_be_removed(app: Any, eleven: FakeElevenLabs) -> None:
    anna = signed_up(app, "anna@example.com")
    boris = signed_up(app, "boris@example.com")
    anna.put("/api/voice", json={"api_key": KEY})
    assert boris.get("/api/voice").json()["enabled"] is False
    assert boris.get("/api/voice/voices").status_code == 409
    assert anna.delete("/api/voice").status_code == 204
    assert anna.get("/api/voice").json()["enabled"] is False


def test_elevenlabs_outage_is_a_plain_message(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(elevenlabs, "transport", httpx.MockTransport(down))
    resp = signed_up(app).put("/api/voice", json={"api_key": KEY})
    assert resp.status_code == 502 and "Нет связи с ElevenLabs" in resp.json()["detail"]
