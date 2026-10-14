"""Building a deck from the render worker.

With RENDERER_URL set (docker compose) the deck is built in the renderer container: the worker
sends the resolved spec over an internal network and reads the files from the shared spool
folder. That container holds no secrets and has no route out, so Chromium never runs next to
the database password. Without RENDERER_URL (development, tests) the same code runs here.
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

logger = logging.getLogger(__name__)

SkillError = RenderError
DeckBuilder = Callable[[str, dict[str, Any], bytes | None, Path], DeckBuild]

__all__ = ["DeckBuild", "DeckBuilder", "SkillError", "build_deck"]


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
    payload = {
        "job": workdir.name,
        "spec": spec_json,
        "theme": theme,
        "logo": base64.b64encode(logo).decode("ascii") if logo is not None else None,
    }
    answer = _post(f"{settings.renderer_url.rstrip('/')}/deck", payload, settings.render_timeout_s)
    return _deck_from(answer, workdir)


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
