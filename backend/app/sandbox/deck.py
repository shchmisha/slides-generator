"""Running the pptx-builder skill's script (skills/pptx-builder, see its SKILL.md).

The skill is called the way an agent calls it: `build-deck --spec --theme [--logo] --out`, as a
separate process with a time limit and a clean environment. The skill itself turns JavaScript
off with CSP and aborts every network request; the renderer container adds isolation around it.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.sandbox.process import clean_env, private_home, run_group

logger = logging.getLogger(__name__)

PREVIEW_NAME = re.compile(r"^slide-\d{2,3}\.png$")
PPTX_NAME = "deck.pptx"


class RenderError(RuntimeError):
    """The file could not be built. The message is safe to show; details go to the log."""


@dataclass
class DeckBuild:
    pptx: Path
    previews: list[Path]
    issues: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    font_warnings: list[str] = field(default_factory=list)


def build_deck(
    spec_json: str,
    theme: dict[str, Any],
    logo: bytes | None,
    workdir: Path,
    *,
    skill_dir: Path,
    timeout_s: int,
) -> DeckBuild:
    if not (skill_dir / "pptx_builder" / "build.py").is_file():
        raise RenderError("Сборщик презентаций не установлен в этом контейнере")
    inputs, out = workdir / "in", workdir / "out"
    for folder in (inputs, out):
        folder.mkdir(parents=True, exist_ok=True)
    (inputs / "spec.json").write_text(spec_json, encoding="utf-8")
    (inputs / "theme.json").write_text(json.dumps(theme, ensure_ascii=False), encoding="utf-8")
    cmd = [sys.executable, "-m", "pptx_builder.build"]
    cmd += ["--spec", str(inputs / "spec.json"), "--theme", str(inputs / "theme.json")]
    cmd += ["--out", str(out)]
    if logo is not None:
        (inputs / "logo.png").write_bytes(logo)
        cmd += ["--logo", str(inputs / "logo.png")]

    with private_home() as home:
        env = clean_env(home, {"PYTHONPATH": str(skill_dir), "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            code, stderr = run_group(cmd, skill_dir, env, timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise RenderError("Сборка презентации заняла слишком много времени") from exc
    if code != 0:
        logger.error("build-deck failed (%d): %s", code, stderr[-4000:])
        raise RenderError("Не удалось собрать презентацию")

    try:
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        previews = [str(name) for name in report["previews"]]
        if report["pptx"] != PPTX_NAME or not all(PREVIEW_NAME.match(n) for n in previews):
            raise ValueError("unexpected output names")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("build-deck wrote an unreadable report: %s", exc)
        raise RenderError("Не удалось собрать презентацию") from exc
    return DeckBuild(
        pptx=out / PPTX_NAME,
        previews=[out / name for name in previews],
        issues=list(report.get("issues", [])),
        timings={k: float(v) for k, v in dict(report.get("timings", {})).items()},
        font_warnings=font_warnings(theme, skill_dir),
    )


@lru_cache
def installed_fonts() -> frozenset[str] | None:
    """Font families known to fontconfig here (lower case); None if it cannot be asked."""
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [fc_list, ":", "family"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    families = {
        name.strip().lower() for line in proc.stdout.splitlines() for name in line.split(",")
    }
    return frozenset(families)


def substitutes(skill_dir: Path) -> dict[str, list[str]]:
    """The skill's own list of metric-compatible fonts (fonts/substitutes.json): it puts them
    into the CSS after each brand font, so this is exactly what the layout falls back to."""
    try:
        data = json.loads((skill_dir / "fonts" / "substitutes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: [str(n) for n in value] for key, value in data.items() if isinstance(value, list)}


def font_warnings(theme: dict[str, Any], skill_dir: Path) -> list[str]:
    """Fonts the layout cannot measure here: PowerPoint will use the real font, so lines may
    break differently than in the preview."""
    families = installed_fonts()
    if families is None:
        return []
    known = substitutes(skill_dir)
    warnings = []
    for name in dict.fromkeys(theme["fonts"].values()):
        key = str(name).lower()
        stand_ins = [n.lower() for n in known.get(key, [])]
        covered = key in families or (stand_ins and all(n in families for n in stand_ins))
        if not covered:
            warnings.append(
                f"Шрифта «{name}» нет на сервере: вёрстка рассчитана по запасному шрифту, "
                "в PowerPoint строки могут переноситься иначе"
            )
    return warnings
