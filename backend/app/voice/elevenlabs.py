"""ElevenLabs API with the user's own key: listing voices, speech with timing, sound effects.

The key travels only in the `xi-api-key` header: never in a URL, a log line or an error
message. httpx exceptions carry the request (headers included), so they are never logged or
re-raised as they are, only turned into a VoiceError with a message for the user.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

VOICE_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")
MP3_FORMAT = "mp3_44100_128"  # constant bitrate: the length can be told from the size
MP3_BYTES_PER_SECOND = 128_000 / 8
SOUND_MODEL = "eleven_text_to_sound_v2"
MAX_AUDIO_BYTES = 8 * 1024 * 1024

# A test can swap in httpx.MockTransport; None means the real network.
transport: httpx.BaseTransport | None = None


class VoiceError(Exception):
    """A failure with a message for the user. `bad_key`: the key itself was refused."""

    def __init__(self, message: str, *, bad_key: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.bad_key = bad_key
        self.status = status


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Alignment:
    """When each character of the text is spoken, in seconds from the start of the clip."""

    chars: list[str]
    starts: list[float]
    ends: list[float]


@dataclass(frozen=True)
class Speech:
    audio: bytes  # MP3
    duration: float
    alignment: Alignment | None


def _client(key: str, timeout: float | None = None) -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        base_url=settings.elevenlabs_base_url.rstrip("/"),
        headers={"xi-api-key": key},
        timeout=timeout or settings.voice_timeout_s,
        transport=transport,
        follow_redirects=False,
    )


def _detail(response: httpx.Response) -> tuple[set[str], str]:
    """(code words such as "invalid_api_key", message) from an ElevenLabs error body. Its
    `status`, `code` and `type` say more than the HTTP status: a wrong key comes back as 400."""
    try:
        detail: Any = response.json().get("detail")
    except (ValueError, AttributeError):
        return set(), ""
    if isinstance(detail, dict):
        words = {str(detail.get(k, "")) for k in ("status", "code", "type")} - {""}
        return words, str(detail.get("message", ""))
    if isinstance(detail, str):
        return set(), detail
    return set(), ""


def _clean(message: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", message).strip()[:200]


def _error(response: httpx.Response) -> VoiceError:
    words, message = _detail(response)
    code = response.status_code
    logger.warning("ElevenLabs answered %d %s", code, ",".join(sorted(words)))
    if words & {"quota_exceeded", "insufficient_credits"} or "quota" in message.lower():
        return VoiceError("На счёте ElevenLabs не хватает кредитов для озвучки", status=code)
    if "missing_permissions" in words:
        return VoiceError(
            "У ключа ElevenLabs нет нужных разрешений: включите для него «Text to Speech», "
            "«Sound Effects» и чтение голосов (Voices: read)",
            bad_key=True,
            status=code,
        )
    if code == 401 or words & {"invalid_api_key", "authentication_error"}:
        return VoiceError(
            "Ключ ElevenLabs не подходит: проверьте его в настройках", bad_key=True, status=code
        )
    if code == 402 or words & {"payment_required", "paid_plan_required"}:
        return VoiceError("Тариф ElevenLabs не позволяет эту операцию", status=code)
    if code == 429:
        return VoiceError("ElevenLabs сейчас перегружен, попробуйте через минуту", status=code)
    if code >= 500:
        return VoiceError("ElevenLabs временно недоступен, попробуйте позже", status=code)
    text = _clean(message)
    return VoiceError(f"ElevenLabs отклонил запрос{': ' + text if text else ''}", status=code)


def _send(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    # `from None`: the httpx exception holds the request, key header included; a traceback of
    # the VoiceError must not print it.
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise VoiceError("ElevenLabs не ответил вовремя, попробуйте ещё раз") from None
    except httpx.HTTPError:
        raise VoiceError("Нет связи с ElevenLabs, попробуйте позже") from None
    if response.status_code >= 400:
        raise _error(response)
    return response


def _describe(labels: Any) -> str:
    if not isinstance(labels, dict):
        return ""
    parts = [labels.get(k) for k in ("gender", "age", "accent", "descriptive", "use_case")]
    return ", ".join(str(p).replace("_", " ") for p in parts if p)[:120]


def list_voices(key: str) -> list[Voice]:
    """The voices this key may use (premade ones and the user's own); also checks the key."""
    with _client(key, timeout=20) as client:
        data = _send(client, "GET", "/v1/voices").json()
    voices = []
    for item in data.get("voices", []) if isinstance(data, dict) else []:
        voice_id, name = str(item.get("voice_id", "")), str(item.get("name", "")).strip()
        if VOICE_ID.match(voice_id) and name:
            voices.append(Voice(voice_id, _clean(name)[:100], _describe(item.get("labels"))))
    return sorted(voices, key=lambda v: v.name.lower())


def _alignment(data: Any) -> Alignment | None:
    if not isinstance(data, dict):
        return None
    chars, starts, ends = (
        data.get(k)
        for k in ("characters", "character_start_times_seconds", "character_end_times_seconds")
    )
    if not (isinstance(chars, list) and isinstance(starts, list) and isinstance(ends, list)):
        return None
    if not chars or not len(chars) == len(starts) == len(ends):
        return None
    try:
        return Alignment(
            [str(c) for c in chars], [float(s) for s in starts], [float(e) for e in ends]
        )
    except (TypeError, ValueError):
        return None


def _checked_audio(audio: bytes) -> bytes:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise VoiceError("ElevenLabs вернул пустой или слишком большой звук")
    return audio


def speak(
    key: str,
    text: str,
    voice_id: str,
    *,
    model: str,
    language: str = "ru",
    seed: int | None = None,
) -> Speech:
    """One clip of narration. Asks for character timing (captions follow the voice); a model
    that cannot give it is asked again without, and its length is told from the MP3 size."""
    if not VOICE_ID.match(voice_id):
        raise VoiceError("Неверный идентификатор голоса")
    body: dict[str, Any] = {"text": text, "model_id": model, "language_code": language}
    if seed is not None:
        body["seed"] = seed
    params = {"output_format": MP3_FORMAT}
    with _client(key) as client:
        try:
            response = _send(
                client,
                "POST",
                f"/v1/text-to-speech/{voice_id}/with-timestamps",
                params=params,
                json=body,
            )
        except VoiceError as exc:
            if exc.bad_key or exc.status not in (400, 404, 422):
                raise
            logger.info("timed speech refused (%s), asking without timing", exc.status)
            plain = _send(
                client, "POST", f"/v1/text-to-speech/{voice_id}", params=params, json=body
            )
            audio = _checked_audio(plain.content)
            return Speech(audio, round(len(audio) / MP3_BYTES_PER_SECOND, 3), None)
    try:
        data = response.json()
        audio = _checked_audio(base64.b64decode(data["audio_base64"], validate=True))
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise VoiceError("ElevenLabs вернул непонятный ответ") from None
    alignment = _alignment(data.get("alignment"))
    duration = alignment.ends[-1] if alignment else len(audio) / MP3_BYTES_PER_SECOND
    return Speech(audio, round(duration, 3), alignment)


def sound(key: str, prompt: str, seconds: float, *, loop: bool = False) -> bytes:
    """A sound effect from a text description (transitions, a quiet background bed)."""
    body: dict[str, Any] = {
        "text": prompt,
        "duration_seconds": seconds,
        "prompt_influence": 0.6,
        "model_id": SOUND_MODEL,
    }
    if loop:
        body["loop"] = True
    with _client(key) as client:
        response = _send(
            client, "POST", "/v1/sound-generation", params={"output_format": MP3_FORMAT}, json=body
        )
    return _checked_audio(response.content)
