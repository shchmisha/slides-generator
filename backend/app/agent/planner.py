"""The agent loop: ask the model for a scenario, check it, send back the exact problems.

One call writes the whole scenario (fewest round trips: speed is graded, and the production
model's tool calling is not guaranteed). The checks are ours: JSON, schema, references,
calculations, typed numbers, slide count, video timing and facts. A model that keeps failing
after MAX_ATTEMPTS gets a clear error instead of a broken deck.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.agent.materials import render_document, render_materials
from app.agent.prompts import deck_system_prompt, video_system_prompt
from app.agent.settings import DeckSettings, VideoSettings
from app.facts.store import FactStore
from app.ingest.models import Document, Note
from app.llm.client import ChatModel, Completion, Message
from app.llm.jsonout import JsonOutputError, extract_json_object
from app.scenario.deck import DeckScenario
from app.scenario.resolve import ResolvedDeck, ScenarioError, resolve_deck
from app.scenario.video import VideoScenario, check_video

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_ERRORS_SHOWN = 30


@dataclass(frozen=True)
class Attempt:
    latency_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    errors: list[str]


@dataclass
class Plan[S: BaseModel]:
    scenario: S
    attempts: list[Attempt]
    raw: str  # the accepted reply, kept for debugging and the demo folder

    @property
    def llm_ms(self) -> float:
        return sum(a.latency_ms for a in self.attempts)


@dataclass
class DeckPlan(Plan[DeckScenario]):
    resolved: ResolvedDeck | None = None


@dataclass
class VideoPlan(Plan[VideoScenario]):
    warnings: list[str] = field(default_factory=list)


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


def repair_message(errors: list[str]) -> str:
    shown = errors[:MAX_ERRORS_SHOWN]
    more = f"\n…и ещё {len(errors) - len(shown)}" if len(errors) > len(shown) else ""
    return (
        "В сценарии есть ошибки. Исправь их все и верни весь JSON целиком, без пояснений:\n"
        + "\n".join(f"- {e}" for e in shown)
        + more
    )


def _run[S: BaseModel](
    model: ChatModel,
    system: str,
    user: str,
    check: _Check[S],
) -> tuple[S, list[Attempt], str]:
    messages: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    attempts: list[Attempt] = []
    errors: list[str] = []
    for number in range(1, MAX_ATTEMPTS + 1):
        completion: Completion = model.complete(messages)
        scenario, errors = check(completion.text)
        if completion.truncated and errors:
            errors.insert(
                0, "Ответ оборвался на середине: сделай тексты короче и верни JSON целиком"
            )
        attempts.append(
            Attempt(
                latency_ms=completion.latency_ms,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
                errors=errors,
            )
        )
        logger.info("planning attempt %d: %d problems", number, len(errors))
        if scenario is not None and not errors:
            return scenario, attempts, completion.text
        messages += [
            {"role": "assistant", "content": completion.text},
            {"role": "user", "content": repair_message(errors)},
        ]
    raise PlanningError(errors, attempts)


class _Check[S: BaseModel]:
    """Parse and validate one reply; returns the scenario (if it parsed) and its problems."""

    def __init__(self, schema: type[S]) -> None:
        self.schema = schema

    def parse(self, text: str) -> tuple[S | None, list[str]]:
        try:
            return self.schema.model_validate(extract_json_object(text)), []
        except JsonOutputError as exc:
            return None, [str(exc)]
        except ValidationError as exc:
            return None, validation_errors(exc)

    def __call__(self, text: str) -> tuple[S | None, list[str]]:
        return self.parse(text)


class _DeckCheck(_Check[DeckScenario]):
    def __init__(self, store: FactStore, settings: DeckSettings) -> None:
        super().__init__(DeckScenario)
        self.store = store
        self.settings = settings
        self.resolved: ResolvedDeck | None = None

    def __call__(self, text: str) -> tuple[DeckScenario | None, list[str]]:
        scenario, errors = self.parse(text)
        if scenario is None:
            return None, errors
        if self.settings.slides and len(scenario.slides) != self.settings.slides:
            errors.append(
                f"slides: нужно ровно {self.settings.slides} слайдов, а их {len(scenario.slides)}"
            )
        try:
            self.resolved = resolve_deck(scenario, self.store)
        except ScenarioError as exc:
            errors += exc.errors
        return scenario, errors


class _VideoCheck(_Check[VideoScenario]):
    def __init__(self, source_text: str, settings: VideoSettings) -> None:
        super().__init__(VideoScenario)
        self.source_text = source_text
        self.settings = settings

    def __call__(self, text: str) -> tuple[VideoScenario | None, list[str]]:
        scenario, errors = self.parse(text)
        if scenario is None:
            return None, errors
        s = self.settings
        return scenario, check_video(scenario, self.source_text, s.min_seconds, s.max_seconds)


def plan_deck(
    documents: list[Document],
    store: FactStore,
    notes: list[Note],
    settings: DeckSettings,
    model: ChatModel,
) -> DeckPlan:
    check = _DeckCheck(store, settings)
    user = (
        render_materials(documents, store.tables, notes)
        + "\n\nСоставь сценарий презентации по этим материалам."
    )
    scenario, attempts, raw = _run(model, deck_system_prompt(settings), user, check)
    return DeckPlan(scenario=scenario, attempts=attempts, raw=raw, resolved=check.resolved)


def source_text(documents: list[Document]) -> str:
    return "\n".join(block.text for doc in documents for block in doc.blocks)


def plan_video(documents: list[Document], settings: VideoSettings, model: ChatModel) -> VideoPlan:
    text = source_text(documents)
    check = _VideoCheck(text, settings)
    materials = (
        "<материалы>\n" + "\n\n".join(render_document(d) for d in documents) + "\n</материалы>"
    )
    user = materials + "\n\nНапиши сценарий ролика по этому тексту."
    scenario, attempts, raw = _run(model, video_system_prompt(settings), user, check)
    return VideoPlan(scenario=scenario, attempts=attempts, raw=raw)
