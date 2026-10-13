"""Running the pptx-builder skill's script (skills/pptx-builder, see its SKILL.md).

The skill is called the way an agent calls it: `build-deck --spec --theme [--logo] --out`, as a
separate process with a time limit. Its environment is built from scratch: no database URL,
no Redis password, no model key. The deck text comes from user files and is rendered in
headless Chromium, so that process must hold nothing worth stealing. The skill itself turns
JavaScript off with CSP and aborts every network request (defence in depth).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Variables the layout step needs (fonts, Chromium); everything else is left behind.
PASSED_ENV = ("PATH", "LANG", "CHROMIUM_PATH", "PLAYWRIGHT_BROWSERS_PATH", "FONTCONFIG_PATH")
_PREVIEW_NAME = re.compile(r"^slide-\d{2,3}\.png$")
_PPTX_NAME = "deck.pptx"


class SkillError(RuntimeError):
    """The deck could not be built. The message is safe to show; details go to the log."""


@dataclass
class DeckBuild:
    pptx: Path
    previews: list[Path]
    issues: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


DeckBuilder = Callable[[str, dict[str, Any], bytes | None, Path], DeckBuild]


def build_deck(
    spec_json: str, theme: dict[str, Any], logo: bytes | None, workdir: Path
) -> DeckBuild:
    settings = get_settings()
    skill = settings.pptx_skill_dir
    if not (skill / "pptx_builder" / "build.py").is_file():
        raise SkillError("Сборщик презентаций не установлен в этом контейнере")
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

    # Chromium's profile, caches and sockets go to a private folder with a short path (a Unix
    # socket path must fit in 108 bytes); outputs stay in `workdir` on the data volume.
    short = "/tmp" if os.access("/tmp", os.W_OK) else None  # noqa: S108 - private mkdtemp
    home = Path(tempfile.mkdtemp(prefix="skill-", dir=short))
    env = {key: os.environ[key] for key in PASSED_ENV if key in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    env |= {
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "TMPDIR": str(home),
        "PYTHONPATH": str(skill),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        code, stderr = _run_group(cmd, skill, env, settings.render_timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise SkillError("Сборка презентации заняла слишком много времени") from exc
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if code != 0:
        logger.error("build-deck failed (%d): %s", code, stderr[-4000:])
        raise SkillError("Не удалось собрать презентацию")

    try:
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        previews = [str(name) for name in report["previews"]]
        if report["pptx"] != _PPTX_NAME or not all(_PREVIEW_NAME.match(n) for n in previews):
            raise ValueError("unexpected output names")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("build-deck wrote an unreadable report: %s", exc)
        raise SkillError("Не удалось собрать презентацию") from exc
    return DeckBuild(
        pptx=out / _PPTX_NAME,
        previews=[out / name for name in previews],
        issues=list(report.get("issues", [])),
        timings={k: float(v) for k, v in dict(report.get("timings", {})).items()},
    )


def _run_group(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> tuple[int, str]:
    """Run in a new process group and kill the whole group on timeout or any interruption:
    build-deck starts Node, which starts Chromium, and none of them may outlive the job."""
    proc = subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    finally:
        # Also after a normal exit: a Chromium helper left behind must not keep running.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    return proc.returncode, stderr


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


def substitutes() -> dict[str, list[str]]:
    """The skill's own list of metric-compatible fonts (fonts/substitutes.json): it puts them
    into the CSS after each brand font, so this is exactly what the layout falls back to."""
    path = get_settings().pptx_skill_dir / "fonts" / "substitutes.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: [str(n) for n in value] for key, value in data.items() if isinstance(value, list)}


def font_warnings(theme: dict[str, Any]) -> list[str]:
    """Fonts the layout cannot measure here: PowerPoint will use the real font, so lines may
    break differently than in the preview."""
    families = installed_fonts()
    if families is None:
        return []
    known = substitutes()
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
