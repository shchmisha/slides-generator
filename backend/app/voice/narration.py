"""Narration for a video with the user's ElevenLabs key: a voice clip per scene, sounds, and
scene lengths fitted to the voice.

Clips are cached per project by (model, voice, text): building the same scenario again, or after
editing one scene, asks ElevenLabs only for what changed, so the user's credits are not spent
twice. Sounds (a background bed and a transition cue) are made once per user and reused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.storage import project_dir, write_file
from app.voice.elevenlabs import Alignment, VoiceError, sound, speak

logger = logging.getLogger(__name__)

LEAD_S = 0.35  # from the scene start to the first word, after the scene has faded in
TAIL_S = 0.45  # after the last word, before the scene fades out
CACHE_VERSION = 1
PARALLEL = 2  # ElevenLabs allows 2 concurrent requests even on the free plan

BED = (
    "bed.mp3",
    "Calm minimal corporate background music, soft warm synth pads and light piano, "
    "steady and unobtrusive, no drums, no vocals, seamless loop",
    20.0,
    True,
)
TRANSITION = (
    "transition.mp3",
    "Soft subtle airy whoosh for a slide transition, clean and short",
    1.0,
    False,
)


@dataclass(frozen=True)
class Clip:
    file: str  # voice-NN.mp3
    audio: bytes
    duration: float
    alignment: Alignment | None


@dataclass
class Narration:
    clips: list[Clip]
    sounds: dict[str, tuple[bytes, float]]  # file name → (MP3, seconds)
    warnings: list[str] = field(default_factory=list)
    synthesized: int = 0  # clips made now; the rest came from the cache
    characters: int = 0  # characters sent to ElevenLabs now


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


def _cached(folder: Path, name: str) -> tuple[bytes, dict[str, Any]] | None:
    try:
        return (folder / f"{name}.mp3").read_bytes(), json.loads(
            (folder / f"{name}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def _alignment_out(alignment: Alignment | None) -> dict[str, Any] | None:
    if alignment is None:
        return None
    return {"chars": alignment.chars, "starts": alignment.starts, "ends": alignment.ends}


def _alignment_in(data: Any) -> Alignment | None:
    if not isinstance(data, dict):
        return None
    return Alignment(list(data["chars"]), list(data["starts"]), list(data["ends"]))


def _clip(
    key: str, text: str, voice_id: str, model: str, folder: Path, number: int
) -> tuple[Clip, str, bool]:
    """(clip, cache name, made now)."""
    name = _digest(CACHE_VERSION, model, voice_id, "ru", text)
    file = f"voice-{number:02d}.mp3"
    hit = _cached(folder, name)
    if hit is not None:
        audio, meta = hit
        return (
            Clip(file, audio, float(meta["duration"]), _alignment_in(meta.get("alignment"))),
            name,
            False,
        )
    speech = speak(key, text, voice_id, model=model, seed=int(name[:8], 16))
    write_file(folder / f"{name}.mp3", speech.audio)
    meta = {"duration": speech.duration, "alignment": _alignment_out(speech.alignment)}
    write_file(folder / f"{name}.json", json.dumps(meta, ensure_ascii=False).encode())
    return Clip(file, speech.audio, speech.duration, speech.alignment), name, True


def _prune(folder: Path, keep: set[str]) -> None:
    """Only the clips of the latest narration stay: the cache never outgrows one video."""
    for path in folder.glob("*"):
        if path.stem not in keep:
            path.unlink(missing_ok=True)


def _sounds(key: str, user_id: uuid.UUID) -> dict[str, tuple[bytes, float]]:
    folder = get_settings().data_dir / "users" / str(user_id) / "sounds"
    made: dict[str, tuple[bytes, float]] = {}
    for file, prompt, seconds, loop in (BED, TRANSITION):
        path = folder / f"{_digest('sound', prompt, seconds, loop)}.mp3"
        if not path.is_file():
            write_file(path, sound(key, prompt, seconds, loop=loop))
        made[file] = (path.read_bytes(), seconds)
    return made


def narrate(
    key: str,
    voice_id: str,
    texts: list[str],
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    with_sounds: bool,
    on_clip: Callable[[int, int], None] = lambda done, total: None,
) -> Narration:
    """A clip per scene text (VoiceError if any fails), then the sounds (a failure there only
    leaves a warning: the voice matters, the music does not)."""
    model = get_settings().voice_model
    folder = project_dir(project_id) / "voice"
    results: list[tuple[Clip, str, bool] | None] = [None] * len(texts)
    with ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="voice") as pool:
        futures = {
            pool.submit(_clip, key, text, voice_id, model, folder, i + 1): i
            for i, text in enumerate(texts)
        }
        try:
            for done, future in enumerate(futures, start=1):
                results[futures[future]] = future.result()
                on_clip(done, len(texts))
        except BaseException:
            for future in futures:  # the first failure stops the rest: no credits spent on them
                future.cancel()
            raise
    made = [r for r in results if r is not None]
    _prune(folder, {name for _, name, _ in made})
    narration = Narration(
        clips=[clip for clip, _, _ in made],
        sounds={},
        synthesized=sum(1 for _, _, new in made if new),
        characters=sum(len(text) for text, (_, _, new) in zip(texts, made, strict=True) if new),
    )
    if with_sounds:
        try:
            narration.sounds = _sounds(key, user_id)
        except VoiceError as exc:
            logger.warning("sounds failed for user %s: %s", user_id, exc)
            narration.warnings.append(
                f"Фоновая музыка и звуки не получились ({exc}): ролик с голосом без них"
            )
    return narration


def fit_timeline(
    planned: list[float], spoken: list[float], max_total: float
) -> tuple[list[float], str | None]:
    """Scene lengths for the voice: each scene at least as long as its clip with a pause on
    both sides; time is taken back from scenes with room to spare so the video keeps to its
    maximum. Works in tenths of a second, so the sum is exact. Returns (lengths, warning)."""
    need = [math.ceil(round((LEAD_S + s + TAIL_S) * 10, 6)) for s in spoken]
    lengths = [max(round(p * 10), n) for p, n in zip(planned, need, strict=True)]
    excess = sum(lengths) - round(max_total * 10)
    if excess > 0:
        slack = [length - n for length, n in zip(lengths, need, strict=True)]
        available = sum(slack)
        cut = [s * min(excess, available) // available if available else 0 for s in slack]
        left = min(excess, available) - sum(cut)
        for i in sorted(range(len(slack)), key=lambda i: slack[i] - cut[i], reverse=True):
            if left <= 0:
                break
            if slack[i] - cut[i] > 0:
                cut[i] += 1
                left -= 1
        lengths = [length - c for length, c in zip(lengths, cut, strict=True)]
    seconds = [length / 10 for length in lengths]
    total = sum(lengths) / 10
    warning = None
    if total > max_total + 1e-9:
        warning = f"С голосом ролик длиннее плана: {total:g} с при максимуме {max_total:g} с"
    return seconds, warning
