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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_args

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

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
from app.facts.models import Operation, Table
from app.facts.store import FactStore
from app.ingest.models import Document, Note
from app.llm.client import ChatModel, Message
from app.llm.jsonout import JsonOutputError, extract_json_object
from app.scenario.deck import DeckScenario, Slide
from app.scenario.lint import lint_deck, unused_tables
from app.scenario.quotes import quote_errors
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
    # What the scenario was checked against, kept so a user's edits get the same checks.
    tables: list[Table] = field(default_factory=list)  # after quarantine
    wanted: tuple[int, int] | None = None  # slide range asked for in the materials
    sources: dict[str, str] = field(default_factory=dict)  # file name → text, for claim quotes


@dataclass
class VideoPlan(Plan[VideoScenario]):
    source_text: str = ""  # after quarantine: the only facts the video may use


OnAttempt = Callable[[int], None]  # called with the attempt number before each model call


class PlanningError(RuntimeError):
    def __init__(self, errors: list[str], attempts: list[Attempt]) -> None:
        super().__init__(
            f"Модель не смогла составить корректный сценарий за {len(attempts)} попытки"
        )
        self.errors = errors
        self.attempts = attempts


def _tags(union: Any, field: str) -> set[str]:
    """The discriminator values of a tagged union (pydantic puts them into error paths)."""
    members = get_args(get_args(union)[0])
    return {str(get_args(m.model_fields[field].annotation)[0]) for m in members}


# "slides.2.key_takeaways.title" → "slides[2].title": the tag and union member names are
# noise for the model and for a person; indexes read like the rest of our messages.
_TAGS = _tags(Slide, "type") | _tags(Operation, "op")


def _path(loc: tuple[int | str, ...]) -> str:
    path = ""
    for i, part in enumerate(loc):
        if isinstance(part, int):
            path += f"[{part}]"
        elif (i > 0 and isinstance(loc[i - 1], int) and part in _TAGS) or not part[:1].islower():
            continue  # a union member: "key_takeaways", "CellRef", "function-after[...]"
        elif "[" in part or "-" in part:
            continue
        else:
            path += f".{part}" if path else part
    return path


def _message(err: ErrorDetails) -> str:
    ctx = err.get("ctx") or {}
    match err["type"]:
        case "missing":
            return "обязательное поле"
        case "string_too_short":
            return "не может быть пустым" if ctx.get("min_length") == 1 else "слишком коротко"
        case "string_too_long":
            return f"слишком длинно: не больше {ctx.get('max_length')} символов"
        case "too_short":
            return f"слишком мало: нужно не меньше {ctx.get('min_length')}"
        case "too_long":
            return f"слишком много: не больше {ctx.get('max_length')}"
        case "literal_error":
            return f"недопустимое значение, можно: {ctx.get('expected')}"
        case "union_tag_invalid":
            return f"неизвестный тип «{ctx.get('tag')}», можно: {ctx.get('expected_tags')}"
        case "extra_forbidden":
            return "лишнее поле"
        case "greater_than_equal":
            return f"не меньше {ctx.get('ge')}"
        case "less_than_equal":
            return f"не больше {ctx.get('le')}"
        case "int_parsing" | "float_parsing" | "int_type" | "float_type":
            return "нужно число"
        case "string_pattern_mismatch":
            return "недопустимое имя: латинские буквы, цифры и _"
        case "value_error":
            return str(err["msg"]).removeprefix("Value error, ")
    return str(err["msg"])


def validation_errors(exc: ValidationError) -> list[str]:
    """Schema errors in the same form as our own checks: "slides[2].title: обязательное поле"."""
    return [
        f"{_path(tuple(err['loc'])) or 'сценарий'}: {_message(err)}"
        for err in exc.errors()[:MAX_ERRORS_SHOWN]
    ]


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
    model: ChatModel, system: str, user: str, check: _Check[S], on_attempt: OnAttempt | None = None
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
        if on_attempt is not None:
            on_attempt(number)
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
        elif best is not None:
            # The polishing round broke a valid scenario: keep that one rather than spend
            # another minute or two of the model's time repairing the repair.
            return best[0], attempts, best[1]
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
        checked = self.parse(text)
        return checked if checked.scenario is None else self.check(checked.scenario)

    def check(self, scenario: S) -> Checked[S]:
        """Checks beyond the schema; also used for a scenario the user edited."""
        return Checked(scenario=scenario)


class DeckCheck(_Check[DeckScenario]):
    def __init__(
        self,
        store: FactStore,
        notes: list[Note],
        settings: DeckSettings,
        wanted: tuple[int, int] | None,
        sources: dict[str, str] | None = None,
        *,
        by_user: bool = False,
    ) -> None:
        super().__init__(DeckScenario)
        self.store = store
        self.notes = notes
        self.settings = settings
        self.wanted = wanted
        self.sources = sources  # None: the materials' text is unknown, quotes are not checked
        # The user's own edit: the slide count asked for in settings is theirs to change.
        self.by_user = by_user

    def check(self, scenario: DeckScenario) -> Checked[DeckScenario]:
        checked = Checked(scenario=scenario)
        try:
            resolved = resolve_deck(
                scenario, self.store, self.notes, auto_data_notes=not self.settings.slides
            )
        except ScenarioError as exc:
            checked.errors += exc.errors
            return checked
        checked.resolved = resolved
        if self.sources is not None:
            checked.errors += quote_errors(scenario.claims, self.sources)
        count = len(resolved.spec.slides)
        wanted = None if self.settings.slides else self.wanted
        checked.findings = lint_deck(scenario, self.store.tables, count, wanted)
        target = self.settings.slides
        if target and count != target:
            # A wish, not correctness: the model is asked once to fix it, and if it cannot, the
            # valid deck is kept with this warning. Seen live: asked for 10 slides, the model
            # wrote 8, and its attempt to add two broke the data references — failing the whole
            # generation after five minutes helped nobody.
            hint = "" if self.by_user else ": добавь или убери слайды"
            if count < target and not self.by_user:
                spare = unused_tables(scenario, self.store.tables)
                if spare:
                    titles = "; ".join(f"[{t.id}] {t.title}" for t in spare[:4])
                    hint += f". Ещё нет на слайдах: {titles}"
            checked.findings.append(f"slides: в настройках {target} слайдов, а их {count}{hint}")
        return checked


class VideoCheck(_Check[VideoScenario]):
    def __init__(self, source_text: str, settings: VideoSettings) -> None:
        super().__init__(VideoScenario)
        self.source_text = source_text
        self.settings = settings
        self.adjustments: list[str] = []

    def check(self, scenario: VideoScenario) -> Checked[VideoScenario]:
        checked = Checked(scenario=scenario)
        s = self.settings
        scenario, fitted = fit_duration(scenario, s.min_seconds, s.max_seconds)
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
    on_attempt: OnAttempt | None = None,
) -> DeckPlan:
    documents, tables, quarantined = _quarantine(documents, store.tables)
    store = FactStore(tables)
    notes = [*notes, *quarantined]
    wanted = (settings.slides, settings.slides) if settings.slides else slide_range(documents)
    sources = document_texts(documents)
    check = DeckCheck(store, notes, settings, wanted, sources)
    user = (
        render_materials(documents, tables, notes, build_digest(tables))
        + f"\n\n{AFTER_MATERIALS}\nСоставь сценарий презентации по этим материалам."
    )
    checked, attempts, raw = _run(
        model, deck_system_prompt(settings, wanted), user, check, on_attempt
    )
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
        tables=tables,
        wanted=wanted,
        sources=sources,
    )


def source_text(documents: list[Document]) -> str:
    return "\n".join(block.text for doc in documents for block in doc.blocks)


def document_texts(documents: list[Document]) -> dict[str, str]:
    """File name → its text (after quarantine): what a claim may quote."""
    texts: dict[str, str] = {}
    for doc in documents:
        texts[doc.filename] = "\n".join(filter(None, [texts.get(doc.filename), source_text([doc])]))
    return texts


def plan_video(
    documents: list[Document],
    settings: VideoSettings,
    model: ChatModel,
    on_attempt: OnAttempt | None = None,
) -> VideoPlan:
    documents, quarantined = quarantine_documents(documents)
    # Numbers allowed in the video come from the text after quarantine: a planted line
    # cannot widen the list of facts.
    text = source_text(documents)
    check = VideoCheck(text, settings)
    materials = (
        "<материалы>\n" + "\n\n".join(render_document(d) for d in documents) + "\n</материалы>"
    )
    user = f"{materials}\n\n{AFTER_MATERIALS}\nНапиши сценарий ролика по этому тексту."
    checked, attempts, raw = _run(model, video_system_prompt(settings), user, check, on_attempt)
    if checked.scenario is None:
        raise PlanningError(checked.errors, attempts)
    return VideoPlan(
        scenario=checked.scenario,
        attempts=attempts,
        raw=raw,
        warnings=checked.findings + check.adjustments[-1:],
        notes=quarantined,
        source_text=text,
    )
