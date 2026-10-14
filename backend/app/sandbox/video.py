"""Running the video-builder skill's script (skills/video-builder, see its SKILL.md): a resolved
video scenario → HyperFrames composition from our templates → MP4 + a poster per scene."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.sandbox.deck import RenderError
from app.sandbox.process import clean_env, private_home, run_group

logger = logging.getLogger(__name__)

VIDEO_NAME = "video.mp4"
POSTER_NAME = re.compile(r"^scene-\d{2}\.png$")


@dataclass
class VideoBuild:
    video: Path
    posters: list[Path]
    duration: float
    timings: dict[str, float] = field(default_factory=dict)


def build_video(
    spec_json: str,
    theme: dict[str, Any],
    logo: bytes | None,
    workdir: Path,
    *,
    skill_dir: Path,
    timeout_s: int,
    workers: int = 1,
) -> VideoBuild:
    if not (skill_dir / "src" / "cli.ts").is_file():
        raise RenderError("Сборщик видео не установлен в этом контейнере")
    inputs, out = workdir / "in", workdir / "out"
    for folder in (inputs, out):
        folder.mkdir(parents=True, exist_ok=True)
    (inputs / "spec.json").write_text(spec_json, encoding="utf-8")
    (inputs / "theme.json").write_text(json.dumps(theme, ensure_ascii=False), encoding="utf-8")
    cmd = ["node", "src/cli.ts", "--spec", str(inputs / "spec.json")]
    cmd += ["--theme", str(inputs / "theme.json"), "--out", str(out), "--workers", str(workers)]
    if logo is not None:
        (inputs / "logo.png").write_bytes(logo)
        cmd += ["--logo", str(inputs / "logo.png")]

    with private_home() as home:
        try:
            code, stderr = run_group(cmd, skill_dir, clean_env(home), timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise RenderError("Запись видео заняла слишком много времени") from exc
    if code != 0:
        logger.error("video render failed (%d): %s", code, stderr[-4000:])
        raise RenderError("Не удалось записать видео")

    try:
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        posters = [str(name) for name in report["posters"]]
        if report["video"] != VIDEO_NAME or not all(POSTER_NAME.match(n) for n in posters):
            raise ValueError("unexpected output names")
        return VideoBuild(
            video=out / VIDEO_NAME,
            posters=[out / name for name in posters],
            duration=float(report["duration"]),
            timings={k: float(v) for k, v in dict(report.get("timings", {})).items()},
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("video render wrote an unreadable report: %s", exc)
        raise RenderError("Не удалось записать видео") from exc
