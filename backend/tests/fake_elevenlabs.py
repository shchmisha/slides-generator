"""A stand-in for the ElevenLabs API on httpx.MockTransport: one valid key, two voices."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

KEY = "sk_test_0123456789abcdef0123456789abcdef"
VOICES = [
    {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "labels": {"gender": "male"}},
    {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah", "labels": {"gender": "female"}},
]
SPEECH_SECONDS_PER_CHAR = 0.06


def fake_mp3(seconds: float) -> bytes:
    """Bytes of the size a 128 kbit/s MP3 of that length has (the code tells length by size)."""
    return b"\xff\xfb" + b"\x00" * (round(seconds * 16_000) - 2)


class FakeElevenLabs:
    def __init__(
        self, *, timestamps: bool = True, credits: bool = True, sound_effects: bool = True
    ) -> None:
        self.timestamps = timestamps
        self.credits = credits
        self.sound_effects = sound_effects
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _error(self, code: int, status: str, message: str = "", kind: str = "") -> httpx.Response:
        detail = {"type": kind, "code": status, "status": status, "message": message}
        return httpx.Response(code, json={"detail": detail})

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body))
        if request.headers.get("xi-api-key") != KEY:
            # As the real API answers (checked): 400, not 401.
            return self._error(
                400, "invalid_api_key", "API key is invalid.", "authentication_error"
            )
        path = request.url.path
        if path == "/v1/voices":
            return httpx.Response(200, json={"voices": VOICES})
        if not self.credits:
            return self._error(401, "quota_exceeded", "This request exceeds your quota")
        if path.startswith("/v1/text-to-speech/"):
            text = body["text"]
            seconds = round(len(text) * SPEECH_SECONDS_PER_CHAR, 3)
            audio = fake_mp3(seconds)
            if path.endswith("/with-timestamps"):
                if not self.timestamps:
                    return self._error(400, "unsupported_model", "timestamps not supported")
                step = seconds / len(text)
                alignment = {
                    "characters": list(text),
                    "character_start_times_seconds": [round(i * step, 3) for i in range(len(text))],
                    "character_end_times_seconds": [
                        round((i + 1) * step, 3) for i in range(len(text))
                    ],
                }
                return httpx.Response(
                    200,
                    json={"audio_base64": base64.b64encode(audio).decode(), "alignment": alignment},
                )
            return httpx.Response(200, content=audio, headers={"content-type": "audio/mpeg"})
        if path == "/v1/sound-generation":
            if not self.sound_effects:
                return self._error(402, "paid_plan_required", "Sound effects need a paid plan")
            return httpx.Response(200, content=fake_mp3(float(body["duration_seconds"])))
        return httpx.Response(404, json={"detail": "not found"})

    def count(self, fragment: str) -> int:
        return sum(1 for _, path, _ in self.calls if fragment in path)
