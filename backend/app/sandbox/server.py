"""The renderer's HTTP interface. Its only client is the render worker, over an internal Docker
network with no route outside (see docker-compose.yml).

    GET  /health
    POST /deck   {"job": "<job uuid>-<attempt>", "spec": "<deck spec JSON text>",
                  "theme": {...}, "logo": "<base64 PNG>" | null}
    POST /video  the same body with a resolved video scenario as "spec"

Files are written to SPOOL_DIR/<job>/ (a volume shared with the worker, not the data volume);
the answer names them relative to that folder:

    200 {"pptx": "out/deck.pptx", "previews": ["out/slide-01.png", …], "issues": [...],
         "timings": {...}, "font_warnings": [...]}
    200 {"video": "out/video.mp4", "posters": ["out/scene-01.png", …], "duration": 72.0,
         "timings": {...}}     for /video; out/progress.json is updated while it records
    422 {"error": "<message for the user>"}   the render ran and failed
    400 malformed request · 404 unknown path · 503 all render slots busy

Configuration comes from a few plain variables (no app.config: this container has no
database or Redis settings to read): SPOOL_DIR, PPTX_SKILL_DIR, VIDEO_SKILL_DIR,
RENDER_TIMEOUT_S, VIDEO_TIMEOUT_S, VIDEO_WORKERS, RENDER_SLOTS, PORT, LOG_LEVEL.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import shutil
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app.sandbox.deck import RenderError, build_deck
from app.sandbox.video import build_video

logger = logging.getLogger("renderer")

JOB_NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-\d{1,3}$")
MAX_BODY_BYTES = 8 * 1024 * 1024  # a spec is ~100 KB; a logo at most 2 MB, base64 ~2.7 MB


class Settings:
    def __init__(self) -> None:
        self.spool = Path(os.environ.get("SPOOL_DIR", "/spool"))
        self.skill_dir = Path(os.environ.get("PPTX_SKILL_DIR", "/opt/skills/pptx-builder"))
        self.timeout_s = int(os.environ.get("RENDER_TIMEOUT_S", "300"))
        self.video_dir = Path(os.environ.get("VIDEO_SKILL_DIR", "/opt/skills/video-builder"))
        self.video_timeout_s = int(os.environ.get("VIDEO_TIMEOUT_S", "900"))
        self.video_workers = int(os.environ.get("VIDEO_WORKERS", "1"))
        self.slots = threading.BoundedSemaphore(int(os.environ.get("RENDER_SLOTS", "2")))


class BadRequestError(ValueError):
    pass


def _job_dir(spool: Path, job: object) -> Path:
    if not isinstance(job, str) or not JOB_NAME.match(job):
        raise BadRequestError("job must be '<uuid>-<attempt>'")
    path = spool / job
    if path.exists():  # an earlier try of the same attempt: start clean
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _inputs(
    settings: Settings, body: dict[str, Any]
) -> tuple[Path, str, dict[str, Any], bytes | None]:
    workdir = _job_dir(settings.spool, body.get("job"))
    spec, theme, logo = body.get("spec"), body.get("theme"), body.get("logo")
    if not isinstance(spec, str) or not isinstance(theme, dict):
        raise BadRequestError("spec must be a string and theme an object")
    try:
        logo_bytes = base64.b64decode(logo, validate=True) if isinstance(logo, str) else None
    except binascii.Error as exc:
        raise BadRequestError("logo must be base64") from exc
    return workdir, spec, theme, logo_bytes


def render_video(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    workdir, spec, theme, logo_bytes = _inputs(settings, body)
    result = build_video(
        spec,
        theme,
        logo_bytes,
        workdir,
        skill_dir=settings.video_dir,
        timeout_s=settings.video_timeout_s,
        workers=settings.video_workers,
    )
    return {
        "video": str(result.video.relative_to(workdir)),
        "posters": [str(p.relative_to(workdir)) for p in result.posters],
        "duration": result.duration,
        "timings": result.timings,
    }


def render_deck(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    workdir, spec, theme, logo_bytes = _inputs(settings, body)
    result = build_deck(
        spec, theme, logo_bytes, workdir, skill_dir=settings.skill_dir, timeout_s=settings.timeout_s
    )
    return {
        "pptx": str(result.pptx.relative_to(workdir)),
        "previews": [str(p.relative_to(workdir)) for p in result.previews],
        "issues": result.issues,
        "timings": result.timings,
        "font_warnings": result.font_warnings,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "renderer"
    sys_version = ""
    settings: Settings  # set by make_server

    def _answer(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._answer(HTTPStatus.OK, {"ok": True})
        else:
            self._answer(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        route = {"/deck": render_deck, "/video": render_video}.get(self.path)
        if route is None:
            self._answer(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY_BYTES:
            self._answer(HTTPStatus.BAD_REQUEST, {"error": "body size"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise BadRequestError("body must be an object")
        except (ValueError, UnicodeDecodeError) as exc:
            self._answer(HTTPStatus.BAD_REQUEST, {"error": f"bad request: {exc}"})
            return
        if not self.settings.slots.acquire(blocking=False):
            self._answer(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "busy"})
            return
        try:
            self._answer(HTTPStatus.OK, route(self.settings, body))
        except BadRequestError as exc:
            self._answer(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except RenderError as exc:
            self._answer(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except Exception:
            logger.exception("render crashed")
            self._answer(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})
        finally:
            self.settings.slots.release()

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), format % args)


def make_server(host: str, port: int, settings: Settings | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"settings": settings or Settings()})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    port = int(os.environ.get("PORT", "8000"))
    server = make_server("0.0.0.0", port)  # noqa: S104 - reachable only on the internal network
    logger.info("renderer listening on :%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
