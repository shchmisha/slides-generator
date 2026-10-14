"""Building a deck or a video from the render worker.

With RENDERER_URL set (docker compose) the file is built in the renderer container: the worker
sends the resolved spec over an internal network and reads the files from the shared spool
folder (a video's progress too, from out/progress.json while it records). That container holds
no secrets and has no route out, so Chromium never runs next to the database password. Without
RENDERER_URL (development, tests) the same code runs here.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.sandbox.deck import PPTX_NAME, PREVIEW_NAME, DeckBuild, RenderError
from app.sandbox.deck import build_deck as build_here
from app.sandbox.video import POSTER_NAME, VIDEO_NAME, VideoBuild
from app.sandbox.video import build_video as record_here

logger = logging.getLogger(__name__)

SkillError = RenderError
DeckBuilder = Callable[[str, dict[str, Any], bytes | None, Path], DeckBuild]
VideoBuilder = Callable[[str, dict[str, Any], bytes | None, Path, dict[str, bytes]], VideoBuild]

__all__ = [
    "DeckBuild",
    "DeckBuilder",
    "SkillError",
    "VideoBuild",
    "VideoBuilder",
    "build_deck",
    "build_video",
]


def build_deck(
    spec_json: str, theme: dict[str, Any], logo: bytes | None, workdir: Path
) -> DeckBuild:
    """`workdir` is SPOOL_DIR/<job uuid>-<attempt>; the renderer writes there too."""
    settings = get_settings()
    if not settings.renderer_url:
        return build_here(
            spec_json,
            theme,
            logo,
            workdir,
            skill_dir=settings.pptx_skill_dir,
            timeout_s=settings.render_timeout_s,
        )
    url = f"{settings.renderer_url.rstrip('/')}/deck"
    answer = _post(url, _payload(spec_json, theme, logo, workdir), settings.render_timeout_s)
    return _deck_from(answer, workdir)


def build_video(
    spec_json: str,
    theme: dict[str, Any],
    logo: bytes | None,
    workdir: Path,
    audio: dict[str, bytes],
) -> VideoBuild:
    """Like build_deck, for a resolved video scenario and its voice clips and sounds (none for
    a silent video); blocks for the whole recording."""
    settings = get_settings()
    if not settings.renderer_url:
        return record_here(
            spec_json,
            theme,
            logo,
            workdir,
            skill_dir=settings.video_skill_dir,
            timeout_s=settings.video_timeout_s,
            audio=audio,
        )
    url = f"{settings.renderer_url.rstrip('/')}/video"
    payload = _payload(spec_json, theme, logo, workdir)
    payload["audio"] = {
        name: base64.b64encode(data).decode("ascii") for name, data in audio.items()
    }
    answer = _post(url, payload, settings.video_timeout_s)
    return _video_from(answer, workdir)


def _payload(
    spec_json: str, theme: dict[str, Any], logo: bytes | None, workdir: Path
) -> dict[str, Any]:
    return {
        "job": workdir.name,
        "spec": spec_json,
        "theme": theme,
        "logo": base64.b64encode(logo).decode("ascii") if logo is not None else None,
    }


def _post(url: str, payload: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - our own internal URL from settings
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s + 30) as response:  # noqa: S310
            answer: dict[str, Any] = json.load(response)
            return answer
    except urllib.error.HTTPError as exc:
        try:
            detail = str(json.load(exc).get("error", ""))
        except (ValueError, AttributeError):
            detail = ""
        if exc.code == 422 and detail:
            raise RenderError(detail) from exc
        if exc.code == 503:
            raise RenderError("Сервис сборки занят, попробуйте через минуту") from exc
        logger.error("renderer answered %d: %s", exc.code, detail)
        raise RenderError("Не удалось собрать файл") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.error("renderer unreachable: %s", exc)
        raise RenderError("Сервис сборки недоступен, попробуйте через минуту") from exc


def _deck_from(answer: dict[str, Any], workdir: Path) -> DeckBuild:
    """Trust only the file names the skill can produce, inside this job's folder."""
    try:
        pptx = str(answer["pptx"])
        previews = [str(p) for p in answer["previews"]]
        names_ok = pptx == f"out/{PPTX_NAME}" and all(
            p.startswith("out/") and PREVIEW_NAME.match(p.removeprefix("out/")) for p in previews
        )
        if not names_ok:
            raise ValueError("unexpected output names")
        return DeckBuild(
            pptx=workdir / pptx,
            previews=[workdir / p for p in previews],
            issues=list(answer.get("issues", [])),
            timings={k: float(v) for k, v in dict(answer.get("timings", {})).items()},
            font_warnings=[str(w) for w in answer.get("font_warnings", [])],
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("renderer answered something unexpected: %s", exc)
        raise RenderError("Не удалось собрать файл") from exc


def _video_from(answer: dict[str, Any], workdir: Path) -> VideoBuild:
    try:
        video = str(answer["video"])
        posters = [str(p) for p in answer["posters"]]
        names_ok = video == f"out/{VIDEO_NAME}" and all(
            p.startswith("out/") and POSTER_NAME.match(p.removeprefix("out/")) for p in posters
        )
        if not names_ok:
            raise ValueError("unexpected output names")
        return VideoBuild(
            video=workdir / video,
            posters=[workdir / p for p in posters],
            duration=float(answer["duration"]),
            timings={k: float(v) for k, v in dict(answer.get("timings", {})).items()},
            audio=answer.get("audio") is True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("renderer answered something unexpected: %s", exc)
        raise RenderError("Не удалось записать видео") from exc
