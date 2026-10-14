"""The user's voice-over settings: their own ElevenLabs key and the voice for their videos.

The key is checked with ElevenLabs, encrypted (app/security/vault.py) and never sent back: no
answer here contains it or any part of it, and request validation errors do not echo input.
Without a key, videos have no sound (the brief: voice only with the user's own key).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import AfterValidator, BaseModel, Field, SecretStr, StringConstraints

from app.api.projects import RedisDep
from app.auth.deps import DB, CurrentUser
from app.config import get_settings
from app.jobs.store import now
from app.models import User, VoiceSettings
from app.security import rate_limit
from app.security.vault import VaultError, seal, unseal
from app.voice.elevenlabs import Voice, VoiceError, list_voices

router = APIRouter(prefix="/voice", tags=["voice"])

VoiceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{1,64}$")]


def _printable(value: SecretStr) -> SecretStr:
    if not re.fullmatch(r"[\x21-\x7e]+", value.get_secret_value()):
        raise ValueError("ключ — одна строка без пробелов")
    return value


class KeyIn(BaseModel):
    api_key: Annotated[SecretStr, Field(min_length=16, max_length=200), AfterValidator(_printable)]
    voice_id: VoiceId | None = None


class VoiceChoiceIn(BaseModel):
    voice_id: VoiceId


class VoiceOut(BaseModel):
    enabled: bool
    voice_id: str | None = None
    voice_name: str | None = None
    updated_at: datetime | None = None
    model: str


class VoiceInfo(BaseModel):
    id: str
    name: str
    description: str


def _out(row: VoiceSettings | None) -> VoiceOut:
    model = get_settings().voice_model
    if row is None:
        return VoiceOut(enabled=False, model=model)
    return VoiceOut(
        enabled=True,
        voice_id=row.voice_id,
        voice_name=row.voice_name,
        updated_at=row.updated_at,
        model=model,
    )


def _http(exc: VoiceError) -> HTTPException:
    code = status.HTTP_422_UNPROCESSABLE_CONTENT if exc.bad_key else status.HTTP_502_BAD_GATEWAY
    return HTTPException(code, detail=str(exc))


def _voices(key: str) -> list[Voice]:
    try:
        return list_voices(key)
    except VoiceError as exc:
        raise _http(exc) from None


def _stored_key(db: DB, user: User) -> tuple[VoiceSettings, str]:
    row = db.get(VoiceSettings, user.id)
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Сначала добавьте ключ ElevenLabs")
    try:
        return row, unseal(user.id, row.api_key)
    except VaultError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Сохранённый ключ не читается (сменился ключ шифрования сервера): "
            "добавьте ключ ElevenLabs заново",
        ) from None


def _pick(voices: list[Voice], *wanted: str | None) -> Voice:
    by_id = {v.id: v for v in voices}
    for voice_id in (*wanted, get_settings().voice_default_id):
        if voice_id and voice_id in by_id:
            return by_id[voice_id]
    if not voices:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="В аккаунте ElevenLabs нет ни одного голоса",
        )
    return voices[0]


@router.get("")
def voice_status(db: DB, user: CurrentUser) -> VoiceOut:
    return _out(db.get(VoiceSettings, user.id))


@router.put("")
def set_key(body: KeyIn, db: DB, user: CurrentUser, redis: RedisDep) -> VoiceOut:
    rate_limit.check(redis, rate_limit.VOICE_PER_USER, str(user.id))
    key = body.api_key.get_secret_value()
    voices = _voices(key)  # also proves the key works before it is stored
    row = db.get(VoiceSettings, user.id)
    voice = _pick(voices, body.voice_id, row.voice_id if row else None)
    if row is None:
        row = VoiceSettings(user_id=user.id, api_key=b"", voice_id="", voice_name="")
        db.add(row)
    row.api_key = seal(user.id, key)
    row.voice_id, row.voice_name = voice.id, voice.name
    row.updated_at = now()
    db.commit()
    return _out(row)


@router.patch("")
def choose_voice(body: VoiceChoiceIn, db: DB, user: CurrentUser, redis: RedisDep) -> VoiceOut:
    rate_limit.check(redis, rate_limit.VOICE_PER_USER, str(user.id))
    row, key = _stored_key(db, user)
    voice = next((v for v in _voices(key) if v.id == body.voice_id), None)
    if voice is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Такого голоса нет")
    row.voice_id, row.voice_name = voice.id, voice.name
    row.updated_at = now()
    db.commit()
    return _out(row)


@router.get("/voices")
def voices(db: DB, user: CurrentUser, redis: RedisDep) -> list[VoiceInfo]:
    rate_limit.check(redis, rate_limit.VOICE_PER_USER, str(user.id))
    _, key = _stored_key(db, user)
    return [VoiceInfo(id=v.id, name=v.name, description=v.description) for v in _voices(key)]


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(db: DB, user: CurrentUser) -> None:
    row = db.get(VoiceSettings, user.id)
    if row is not None:
        db.delete(row)
        db.commit()
