"""The agent loop: ask the model for a scenario, check it, send back the exact problems.

One call writes the whole scenario (fewest round trips: speed is graded, and the production
model's tool calling is not guaranteed). Before the call, code removes instructions planted in
the materials and computes a data summary. After it, two kinds of problems are found:
- errors (JSON, schema, references, calculations, typed numbers, an explicit slide count,
  video timing and facts) — the scenario cannot be used; repaired until MAX_ATTEMPTS;
- findings (wording that contradicts the data, the slide count asked for in the materials,
  ad wording in the video) — sent back once; if they remain, the scenario is still accepted
  and they are listed as warnings. A rule of thumb must never cost the user a deck.
The best acceptable reply is kept, so a repair round that makes things worse cannot lose it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.agent.materials import render_document, render_materials
from app.agent.prompts import (
    AFTER_MATERIALS,
    deck_system_prompt,
    video_system_prompt,
)
from app.agent.quarantine import quarantine_documents, quarantine_tables
from app.agent.requirements import slide_range
from app.agent.settings import DeckSettings, VideoSettings
from app.facts.digest import build_digest
from app.facts.models import Table
from app.facts.store import FactStore
from app.ingest.models import Document, Note
from app.llm.client import ChatModel, Message
from app.llm.jsonout import JsonOutputError, extract_json_object
from app.scenario.deck import DeckScenario
from app.scenario.lint import lint_deck
from app.scenario.resolve import ResolvedDeck, ScenarioError, resolve_deck
from app.scenario.video import VideoScenario, check_video, fit_duration, lint_video

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_ERRORS_SHOWN = 30


@dataclass(frozen=True)
class Attempt:
    latency_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    errors: list[str]
    findings: list[str] = field(default_factory=list)
    reply: str = ""  # the raw model reply, kept for debugging and regression fixtures


@dataclass
class Checked[S: BaseModel]:
    scenario: S | None
    errors: list[str] = field(default_factory=list)  # must be fixed
    findings: list[str] = field(default_factory=list)  # should be fixed
    resolved: ResolvedDeck | None = None


@dataclass
class Plan[S: BaseModel]:
    scenario: S
    attempts: list[Attempt]
    raw: str  # the accepted reply, kept for debugging and the demo folder
    warnings: list[str] = field(default_factory=list)  # findings left after the repair round
    notes: list[Note] = field(default_factory=list)  # data notes, quarantined instructions

    @property
    def llm_ms(self) -> float:
        return sum(a.latency_ms for a in self.attempts)


@dataclass
class DeckPlan(Plan[DeckScenario]):
    resolved: ResolvedDeck | None = None


@dataclass
class VideoPlan(Plan[VideoScenario]):
    pass


class PlanningError(RuntimeError):
    def __init__(self, errors: list[str], attempts: list[Attempt]) -> None:
        super().__init__(
            f"Модель не смогла составить корректный сценарий за {len(attempts)} попытки"
        )
        self.errors = errors
        self.attempts = attempts


def validation_errors(exc: ValidationError) -> list[str]:
    errors = []
    for err in exc.errors()[:MAX_ERRORS_SHOWN]:
        path = ".".join(str(part) for part in err["loc"])
        errors.append(f"{path or 'сценарий'}: {err['msg']}")
    return errors


def _section(title: str, items: list[str]) -> str:
    shown = items[:MAX_ERRORS_SHOWN]
    more = f"\n…и ещё {len(items) - len(shown)}" if len(items) > len(shown) else ""
    return f"{title}\n" + "\n".join(f"- {item}" for item in shown) + more


def repair_message(errors: list[str], findings: list[str] | None = None) -> str:
    parts = []
    if errors:
        parts.append(_section("В сценарии есть ошибки. Исправь их все:", errors))
    if findings:
        parts.append(
            _section("Формулировки расходятся с данными или с просьбами из материалов:", findings)
        )
    return "\n\n".join(parts) + "\n\nВерни весь JSON целиком, без пояснений."


def _run[S: BaseModel](
    model: ChatModel, system: str, user: str, check: _Check[S]
) -> tuple[Checked[S], list[Attempt], str]:
    messages: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    attempts: list[Attempt] = []
    best: tuple[Checked[S], str] | None = None
    soft_round_used = False
    checked: Checked[S] = Checked(scenario=None)
    for number in range(1, MAX_ATTEMPTS + 1):
        completion = model.complete(messages)
        checked = check(completion.text)
        if completion.truncated and checked.errors:
            checked.errors.insert(
                0, "Ответ оборвался на середине: сделай тексты короче и верни JSON целиком"
            )
        attempts.append(
            Attempt(
                latency_ms=completion.latency_ms,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
                errors=checked.errors,
                findings=checked.findings,
                reply=completion.text,
            )
        )
        logger.info(
            "planning attempt %d: %d errors, %d findings",
            number,
            len(checked.errors),
            len(checked.findings),
        )
        acceptable = checked.scenario is not None and not checked.errors
        if acceptable:
            best = (checked, completion.text)
            if not checked.findings or soft_round_used or number == MAX_ATTEMPTS:
                return checked, attempts, completion.text
            soft_round_used = True
            repair = repair_message([], checked.findings)
        else:
            repair = repair_message(checked.errors, checked.findings)
        messages += [
            {"role": "assistant", "content": completion.text},
            {"role": "user", "content": repair},
        ]
    if best is not None:
        return best[0], attempts, best[1]
    raise PlanningError(checked.errors, attempts)


class _Check[S: BaseModel]:
    """Parse and validate one reply."""

    def __init__(self, schema: type[S]) -> None:
        self.schema = schema

    def parse(self, text: str) -> Checked[S]:
        try:
            return Checked(scenario=self.schema.model_validate(extract_json_object(text)))
        except JsonOutputError as exc:
            return Checked(scenario=None, errors=[str(exc)])
        except ValidationError as exc:
            return Checked(scenario=None, errors=validation_errors(exc))

    def __call__(self, text: str) -> Checked[S]:
        return self.parse(text)


class _DeckCheck(_Check[DeckScenario]):
    def __init__(
        self,
        store: FactStore,
        notes: list[Note],
        settings: DeckSettings,
        wanted: tuple[int, int] | None,
    ) -> None:
        super().__init__(DeckScenario)
        self.store = store
        self.notes = notes
        self.settings = settings
        self.wanted = wanted

    def __call__(self, text: str) -> Checked[DeckScenario]:
        checked = self.parse(text)
        scenario = checked.scenario
        if scenario is None:
            return checked
        try:
            resolved = resolve_deck(
                scenario, self.store, self.notes, auto_data_notes=not self.settings.slides
            )
        except ScenarioError as exc:
            checked.errors += exc.errors
            return checked
        checked.resolved = resolved
        count = len(resolved.spec.slides)
        if self.settings.slides and count != self.settings.slides:
            checked.errors.append(
                f"slides: нужно ровно {self.settings.slides} слайдов, а их {count}"
            )
        wanted = None if self.settings.slides else self.wanted
        checked.findings = lint_deck(scenario, self.store.tables, count, wanted)
        return checked


class _VideoCheck(_Check[VideoScenario]):
    def __init__(self, source_text: str, settings: VideoSettings) -> None:
        super().__init__(VideoScenario)
        self.source_text = source_text
        self.settings = settings
        self.adjustments: list[str] = []

    def __call__(self, text: str) -> Checked[VideoScenario]:
        checked = self.parse(text)
        if checked.scenario is None:
            return checked
        s = self.settings
        scenario, fitted = fit_duration(checked.scenario, s.min_seconds, s.max_seconds)
        checked.scenario = scenario
        checked.errors = check_video(scenario, self.source_text, s.min_seconds, s.max_seconds)
        checked.findings = lint_video(scenario, self.source_text)
        if fitted:
            self.adjustments.append(fitted)
        return checked


def _quarantine(
    documents: list[Document], tables: list[Table]
) -> tuple[list[Document], list[Table], list[Note]]:
    documents, document_notes = quarantine_documents(documents)
    tables, table_notes = quarantine_tables(tables)
    return documents, tables, document_notes + table_notes


def plan_deck(
    documents: list[Document],
    store: FactStore,
    notes: list[Note],
    settings: DeckSettings,
    model: ChatModel,
) -> DeckPlan:
    documents, tables, quarantined = _quarantine(documents, store.tables)
    store = FactStore(tables)
    notes = [*notes, *quarantined]
    wanted = (settings.slides, settings.slides) if settings.slides else slide_range(documents)
    check = _DeckCheck(store, notes, settings, wanted)
    user = (
        render_materials(documents, tables, notes, build_digest(tables))
        + f"\n\n{AFTER_MATERIALS}\nСоставь сценарий презентации по этим материалам."
    )
    checked, attempts, raw = _run(model, deck_system_prompt(settings, wanted), user, check)
    if checked.scenario is None:  # _run returns only acceptable replies
        raise PlanningError(checked.errors, attempts)
    resolved = checked.resolved
    warnings = (resolved.warnings if resolved else []) + checked.findings
    return DeckPlan(
        scenario=checked.scenario,
        attempts=attempts,
        raw=raw,
        warnings=warnings,
        notes=notes,
        resolved=resolved,
    )


def source_text(documents: list[Document]) -> str:
    return "\n".join(block.text for doc in documents for block in doc.blocks)


def plan_video(documents: list[Document], settings: VideoSettings, model: ChatModel) -> VideoPlan:
    documents, quarantined = quarantine_documents(documents)
    # Numbers allowed in the video come from the text after quarantine: a planted line
    # cannot widen the list of facts.
    text = source_text(documents)
    check = _VideoCheck(text, settings)
    materials = (
        "<материалы>\n" + "\n\n".join(render_document(d) for d in documents) + "\n</материалы>"
    )
    user = f"{materials}\n\n{AFTER_MATERIALS}\nНапиши сценарий ролика по этому тексту."
    checked, attempts, raw = _run(model, video_system_prompt(settings), user, check)
    if checked.scenario is None:
        raise PlanningError(checked.errors, attempts)
    return VideoPlan(
        scenario=checked.scenario,
        attempts=attempts,
        raw=raw,
        warnings=checked.findings + check.adjustments[-1:],
        notes=quarantined,
    )
