"""What a scenario is checked against, and the report shown on the review step.

The model's reply and every edit the user makes go through the same checks (`DeckCheck`,
`VideoCheck`). To make that possible after the model call, each scenario version stores its
*basis*: the cleaned tables and data notes for a deck, the source text for a video, and the
settings and slide range it was planned with. Files uploaded later do not change it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.planner import DeckCheck, DeckPlan, VideoCheck, VideoPlan, validation_errors
from app.agent.settings import DeckSettings, VideoSettings
from app.facts.models import Derived, Quantity, Table
from app.facts.store import FactStore
from app.ingest.models import Note
from app.scenario.deck import DeckScenario
from app.scenario.resolve import ResolvedDeck
from app.scenario.video import VideoScenario

# A scenario is a page or two of JSON; anything far bigger is not an edit of one.
MAX_SCENARIO_CHARS = 200_000


class _Basis(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeckBasis(_Basis):
    kind: Literal["deck"] = "deck"
    tables: list[Table]
    notes: list[Note]
    settings: DeckSettings
    wanted: tuple[int, int] | None = None
    sources: dict[str, str] | None = None  # file name → text: what a claim may quote


class VideoBasis(_Basis):
    kind: Literal["video"] = "video"
    source_text: str
    settings: VideoSettings
    notes: list[Note] = Field(default_factory=list)  # e.g. removed instructions, for the user


Basis = Annotated[DeckBasis | VideoBasis, Field(discriminator="kind")]


@dataclass
class Review:
    """The outcome of checking one scenario. `content` is what gets stored: for a video the
    scene durations may have been fitted to the length range."""

    content: dict[str, Any] | None
    errors: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.content is not None and not self.errors


def _notes(notes: list[Note]) -> list[dict[str, Any]]:
    return [note.model_dump() for note in notes]


def deck_report(
    resolved: ResolvedDeck | None, warnings: list[str], notes: list[Note]
) -> dict[str, Any]:
    """`warnings`: calculation warnings and wording findings left after the checks."""
    warnings = list(dict.fromkeys(warnings))  # the same note can come from two slides
    if resolved is None:
        return {"warnings": warnings, "notes": _notes(notes)}
    return {
        "warnings": warnings,
        "notes": _notes(notes),
        "claims": [asdict(claim) for claim in resolved.claims],
        "slides": len(resolved.spec.slides),
        # The slides with every number filled in: what the user actually reviews.
        "preview": resolved.spec.model_dump(mode="json", by_alias=True, exclude_none=True),
        "preview_origins": resolved.origins,
        # Every name a text may use, with its figure: shown in place of {placeholders} and
        # offered in the editor, so a user adds numbers from the data, never typed ones.
        "values": [_value(d) for d in resolved.derived],
    }


def _value(derived: Derived) -> dict[str, Any]:
    result = derived.result
    if isinstance(result, Quantity):
        return {
            "name": derived.id,
            "label": derived.label,
            "value": result.formatted(),
            "signed": result.formatted(signed=True),
        }
    return {
        "name": derived.id,
        "label": derived.label,
        "items": [
            {
                "key": item.key,
                "value": item.quantity.formatted(),
                "signed": item.quantity.formatted(signed=True),
            }
            for item in result.items
        ],
    }


def video_report(
    scenario: VideoScenario, warnings: list[str], notes: list[Note], settings: VideoSettings
) -> dict[str, Any]:
    return {
        "warnings": list(dict.fromkeys(warnings)),
        "notes": _notes(notes),
        "seconds": round(sum(scene.duration_s for scene in scenario.scenes), 1),
        "scenes": len(scenario.scenes),
        # The range this scenario is checked against: the settings at planning time.
        "min_seconds": settings.min_seconds,
        "max_seconds": settings.max_seconds,
    }


def deck_basis(plan: DeckPlan, settings: DeckSettings) -> DeckBasis:
    return DeckBasis(
        tables=plan.tables,
        notes=plan.notes,
        settings=settings,
        wanted=plan.wanted,
        sources=plan.sources,
    )


def video_basis(plan: VideoPlan, settings: VideoSettings) -> VideoBasis:
    return VideoBasis(source_text=plan.source_text, settings=settings, notes=plan.notes)


def review(content: dict[str, Any], basis: DeckBasis | VideoBasis) -> Review:
    if isinstance(basis, DeckBasis):
        return _review_deck(content, basis)
    return _review_video(content, basis)


def _review_deck(content: dict[str, Any], basis: DeckBasis) -> Review:
    try:
        scenario = DeckScenario.model_validate(content)
    except ValidationError as exc:
        return Review(content=None, errors=validation_errors(exc))
    check = DeckCheck(
        FactStore(basis.tables),
        basis.notes,
        basis.settings,
        basis.wanted,
        basis.sources,
        by_user=True,
    )
    checked = check.check(scenario)
    resolved = checked.resolved
    warnings = (resolved.warnings if resolved else []) + checked.findings
    return Review(
        content=scenario.model_dump(mode="json", exclude_none=True),
        errors=checked.errors,
        report=deck_report(resolved, warnings, basis.notes),
    )


def _review_video(content: dict[str, Any], basis: VideoBasis) -> Review:
    try:
        scenario = VideoScenario.model_validate(content)
    except ValidationError as exc:
        return Review(content=None, errors=validation_errors(exc))
    check = VideoCheck(basis.source_text, basis.settings)
    checked = check.check(scenario)
    fitted = checked.scenario or scenario
    warnings = checked.findings + check.adjustments[-1:]
    return Review(
        content=fitted.model_dump(mode="json", exclude_none=True),
        errors=checked.errors,
        report=video_report(fitted, warnings, basis.notes, basis.settings),
    )
