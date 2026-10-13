"""Run the planner locally against the configured model (no database, no queue):

    python -m app.agent.cli deck q3_metrics.xlsx manager_comments.docx --out out/ --env-file ../.env
    python -m app.agent.cli video superservice.txt --out out/ --env-file ../.env

Writes scenario.json (what the model wrote), deck_spec.json (deck only, for build-deck) and
attempts.json (timings, token counts, problems sent back to the model).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


def load_env_file(path: Path) -> None:
    """KEY=VALUE lines into the environment, without overriding what is already set."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.agent.cli")
    parser.add_argument("kind", choices=["deck", "video"])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--slides", type=int)
    args = parser.parse_args(argv)

    if args.env_file:
        load_env_file(args.env_file)
    # The planner needs no database or queue, but Settings requires their URLs.
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused@localhost:1/unused")
    os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")

    from app.agent.planner import PlanningError, plan_deck, plan_video
    from app.agent.settings import DeckSettings, VideoSettings
    from app.facts.build import build_facts
    from app.ingest.extract import extract_file
    from app.llm.client import OpenAICompatibleModel

    documents = [extract_file(path.read_bytes(), path.name) for path in args.files]
    model = OpenAICompatibleModel()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        if args.kind == "deck":
            store, notes = build_facts(documents)
            plan = plan_deck(documents, store, notes, DeckSettings(slides=args.slides), model)
            if plan.resolved is None:  # plan_deck only returns resolved scenarios
                raise PlanningError(["сценарий не разрешён"], plan.attempts)
            (args.out / "deck_spec.json").write_text(plan.resolved.spec.to_json(), encoding="utf-8")
            summary: dict[str, object] = {
                "claims": [asdict(c) for c in plan.resolved.claims],
                "warnings": plan.warnings,
                "notes": [n.model_dump() for n in plan.notes],
                "slides": len(plan.resolved.spec.slides),
            }
            scenario_json = plan.scenario.model_dump_json(indent=1)
            attempts = plan.attempts
        else:
            video = plan_video(documents, VideoSettings(), model)
            summary = {
                "seconds": sum(scene.duration_s for scene in video.scenario.scenes),
                "warnings": video.warnings,
                "notes": [n.model_dump() for n in video.notes],
            }
            scenario_json = video.scenario.model_dump_json(indent=1)
            attempts = video.attempts
    except PlanningError as exc:
        failure = {
            "error": str(exc),
            "errors": exc.errors,
            "attempts": [asdict(a) for a in exc.attempts],
        }
        (args.out / "attempts.json").write_text(json.dumps(failure, ensure_ascii=False, indent=1))
        print(json.dumps(failure, ensure_ascii=False, indent=1), file=sys.stderr)
        return 1

    (args.out / "scenario.json").write_text(scenario_json, encoding="utf-8")
    report: dict[str, object] = {"attempts": [asdict(a) for a in attempts], **summary}
    (args.out / "attempts.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
