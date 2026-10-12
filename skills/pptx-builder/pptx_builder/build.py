"""build_deck: the script the agent calls to turn a render-ready deck spec into a PPTX.

    build-deck --spec deck.json --theme theme.json [--logo logo.png] --out out/
    build-deck --from-layout out/layout.json --out out/      # re-emit without re-layout

Steps: Node + Chromium lay out the slides and save previews (src/cli.ts → layout.json), then
python-pptx writes deck.pptx from layout.json. Writes report.json and prints it: layout issues
(overflow, shrunk text) are reported, not fatal, so the agent can shorten text and run again.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter

from pydantic import ValidationError

from pptx_builder.emit import EmitError, write_pptx
from pptx_builder.layout import Layout

SKILL_DIR = Path(__file__).resolve().parent.parent
LAYOUT_SCRIPT = SKILL_DIR / "src" / "cli.ts"
LAYOUT_TIMEOUT_S = 120
# The layout step renders untrusted text in Chromium: it gets no secrets from our environment.
PASSED_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "CHROMIUM_PATH", "PLAYWRIGHT_BROWSERS_PATH")


class BuildError(RuntimeError):
    """The deck could not be built; the message is safe to show to the agent."""


@dataclass
class Report:
    pptx: str
    previews: list[str]
    issues: list[dict[str, object]]
    timings: dict[str, float] = field(default_factory=dict)


def run_layout(spec: Path, theme: Path, out_dir: Path, logo: Path | None = None) -> Path:
    node = shutil.which("node")
    if node is None:
        raise BuildError("node is not installed")
    cmd = [node, str(LAYOUT_SCRIPT), "--spec", str(spec), "--theme", str(theme)]
    cmd += ["--out", str(out_dir)]
    if logo is not None:
        cmd += ["--logo", str(logo)]
    env = {key: os.environ[key] for key in PASSED_ENV if key in os.environ}
    try:
        proc = subprocess.run(
            cmd,
            cwd=SKILL_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=LAYOUT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"layout timed out after {LAYOUT_TIMEOUT_S} s") from exc
    if proc.returncode != 0:
        raise BuildError(proc.stderr.strip()[-2000:] or f"layout exited with {proc.returncode}")
    return out_dir / "layout.json"


def emit(layout_path: Path, out_dir: Path) -> Report:
    try:
        layout = Layout.model_validate_json(layout_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise BuildError(f"layout.json is invalid: {exc}") from exc
    started = perf_counter()
    pptx_path = out_dir / "deck.pptx"
    try:
        write_pptx(layout, pptx_path)
    except EmitError as exc:
        raise BuildError(str(exc)) from exc
    timings = dict(layout.timings)
    timings["pptx_ms"] = (perf_counter() - started) * 1000
    return Report(
        pptx=pptx_path.name,
        previews=layout.previews,
        issues=[issue.model_dump() for issue in layout.issues],
        timings=timings,
    )


def build_deck(spec: Path, theme: Path, out_dir: Path, logo: Path | None = None) -> Report:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    layout_path = run_layout(spec, theme, out_dir, logo)
    layout_total_ms = (perf_counter() - started) * 1000
    report = emit(layout_path, out_dir)
    report.timings["layout_total_ms"] = layout_total_ms
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build-deck", description=__doc__.split("\n")[0])
    parser.add_argument("--spec", type=Path, help="render-ready deck spec (JSON)")
    parser.add_argument("--theme", type=Path, help="theme with every color token and fonts")
    parser.add_argument("--logo", type=Path, help="PNG logo")
    parser.add_argument("--from-layout", type=Path, help="skip layout, emit from layout.json")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    args = parser.parse_args(argv)

    try:
        if args.from_layout:
            report = emit(args.from_layout, args.out)
        elif args.spec and args.theme:
            report = build_deck(args.spec, args.theme, args.out, args.logo)
        else:
            parser.error("pass --spec and --theme, or --from-layout")
    except BuildError as exc:
        print(f"build_deck failed: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(asdict(report), ensure_ascii=False, indent=2)
    (args.out / "report.json").write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
