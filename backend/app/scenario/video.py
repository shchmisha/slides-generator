"""Video scenario: scenes for a 60–75 s explainer, every fact taken from the source text.

The model writes narration and picks a visual type per scene; HTML is never written by the
model (scenes are rendered from our own HyperFrames templates).
"""

import math
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.scenario.clean import clean_text
from app.scenario.timing import give, take_back

ShortText = Annotated[str, Field(min_length=1, max_length=90), AfterValidator(clean_text)]
Narration = Annotated[str, Field(min_length=1, max_length=420), AfterValidator(clean_text)]

MIN_TOTAL_S = 60
MAX_TOTAL_S = 75
# Calm Russian narration is about 2.3 words per second; allow a little more before failing.
MAX_WORDS_PER_SECOND = 2.6
# Russian words are long: a calm voice says about 4–5 syllables a second.
MAX_SYLLABLES_PER_SECOND = 5.5
_VOWELS = re.compile("[аеёиоуыэюяАЕЁИОУЫЭЮЯ]")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Visual(_Model):
    type: Literal["title", "statement", "bullets", "big_number", "comparison", "steps", "closing"]
    heading: ShortText | None = None
    items: list[ShortText] = Field(default_factory=list, max_length=5)
    number: ShortText | None = None  # for big_number: a figure exactly as in the source text
    caption: ShortText | None = None


class Scene(_Model):
    duration_s: float = Field(ge=3, le=15)
    narration: Narration
    visual: Visual


class VideoScenario(_Model):
    kind: Literal["video"]
    title: ShortText
    scenes: list[Scene] = Field(min_length=4, max_length=14)


_NUMBER = re.compile(r"\d+(?:[  ]\d{3})*(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[  ]", "", n).replace(",", ".") for n in _NUMBER.findall(text)}


MIN_SCENE_S = 3
MAX_SCENE_S = 15


def _needed_tenths(scene: Scene) -> int:
    words = len(scene.narration.split())
    return max(MIN_SCENE_S * 10, math.ceil(round(words / MAX_WORDS_PER_SECOND * 10, 6)))


def fit_duration(
    scenario: VideoScenario,
    min_total_s: float = MIN_TOTAL_S,
    max_total_s: float = MAX_TOTAL_S,
) -> tuple[VideoScenario, str | None]:
    """Scene lengths that fit both the words and the allowed total. The model often lands at
    78–80 s for a 60–75 s limit, or gives one scene more words than its seconds allow, and
    repeats it when asked to fix it (seen live); code adds numbers better than a language
    model. A scene too short for its words gets the time it needs, taken back from scenes
    with time to spare. Only when the words cannot fit at all does the check report it, and
    the model shortens the text."""
    scenes = scenario.scenes
    need = [_needed_tenths(scene) for scene in scenes]
    planned = [round(scene.duration_s * 10) for scene in scenes]
    low, high = round(min_total_s * 10), round(max_total_s * 10)
    if sum(need) > high or max(need) > MAX_SCENE_S * 10:
        return scenario, None
    lengths = [max(p, n) for p, n in zip(planned, need, strict=True)]
    if sum(lengths) > high:
        lengths = take_back(lengths, need, sum(lengths) - high)
    elif sum(lengths) < low:
        lengths = give(lengths, [MAX_SCENE_S * 10] * len(lengths), low - sum(lengths))
    if lengths == planned or not low <= sum(lengths) <= high:
        return scenario, None
    fitted = scenario.model_copy(
        update={
            "scenes": [
                scene.model_copy(update={"duration_s": length / 10})
                for scene, length in zip(scenes, lengths, strict=True)
            ]
        }
    )
    before, after = sum(planned) / 10, sum(lengths) / 10
    if before == after:
        return fitted, "Длительности сцен подогнаны кодом под объём текста"
    return fitted, f"Длительность сцен подогнана кодом: {before:g} → {after:g} с"


def check_video(
    scenario: VideoScenario,
    source_text: str,
    min_total_s: float = MIN_TOTAL_S,
    max_total_s: float = MAX_TOTAL_S,
) -> list[str]:
    """Problems for the model to fix; empty when the scenario can be rendered."""
    errors: list[str] = []
    total = sum(scene.duration_s for scene in scenario.scenes)
    if not min_total_s <= total <= max_total_s:
        errors.append(f"Общая длительность {total:g} с, а нужно {min_total_s:g}–{max_total_s:g} с")
    allowed = _numbers(source_text)
    for i, scene in enumerate(scenario.scenes):
        words = len(scene.narration.split())
        if words > scene.duration_s * MAX_WORDS_PER_SECOND:
            errors.append(
                f"scenes[{i}].narration: {words} слов не успеть сказать за {scene.duration_s:g} с "
                f"(не больше {int(scene.duration_s * MAX_WORDS_PER_SECOND)})"
            )
        visual = scene.visual
        texts = [scene.narration, visual.heading or "", visual.number or "", visual.caption or ""]
        texts += visual.items
        invented = sorted(n for text in texts for n in _numbers(text) if n not in allowed)
        if invented:
            errors.append(
                f"scenes[{i}]: числа {', '.join(invented)} нет в исходном тексте — "
                "используйте только факты из текста"
            )
        if visual.type == "big_number" and not visual.number:
            errors.append(f"scenes[{i}].visual: для big_number нужно поле number")
        if visual.type in ("bullets", "steps", "comparison") and len(visual.items) < 2:
            errors.append(f"scenes[{i}].visual: для {visual.type} нужно 2–5 пунктов в items")
    return errors


_ABBREVIATION = re.compile(r"\b[А-ЯЁ]{2,5}\b")
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|https?://\S+")

# Ad clichés and claims the brief rules out unless the source itself says them.
_AD_WORDS = re.compile(
    r"\b(хотите|знакомьтесь|присоединяйтесь|подключайтесь (?:сегодня|сейчас|скорее)"
    r"|спецпредложени\w*|скидк\w*|навсегда|комисси\w*|экономьте|экономи\w* времени|комфорт\w*"
    r"|уже с нами|почему выбирают|лучш\w*|выгодн\w*|уникальн\w*|не упустите|успейте"
    r"|автоматическ\w*)\b",
    re.IGNORECASE,
)


def lint_video(scenario: VideoScenario, source_text: str) -> list[str]:
    """Soft findings: ad wording, exclamations and claims the source does not make."""
    source = source_text.casefold()
    findings = []
    for i, scene in enumerate(scenario.scenes):
        visual = scene.visual
        texts = [scene.narration, visual.heading or "", visual.caption or "", *visual.items]
        joined = " ".join(texts)
        syllables = len(_VOWELS.findall(scene.narration))
        if syllables > scene.duration_s * MAX_SYLLABLES_PER_SECOND:
            findings.append(
                f"scenes[{i}].narration: {syllables} слогов за {scene.duration_s:g} с — диктору "
                "придётся спешить; сократи текст сцены"
            )
        for match in _AD_WORDS.finditer(joined):
            if match.group(0).casefold() not in source:
                findings.append(
                    f"scenes[{i}]: «{match.group(0)}» — рекламный штамп или то, чего нет в тексте. "
                    "Пиши спокойно и только о том, что сказано в источнике"
                )
        if "!" in joined:
            findings.append(f"scenes[{i}]: восклицательный знак — тон должен быть спокойным")
        unknown = sorted({a for a in _ABBREVIATION.findall(joined) if a not in source_text})
        if unknown:
            findings.append(
                f"scenes[{i}]: сокращения {', '.join(unknown)} — в тексте их нет; пиши словами"
            )
    everything = " ".join(
        " ".join([s.narration, s.visual.heading or "", s.visual.caption or "", *s.visual.items])
        for s in scenario.scenes
    )
    for address in _ADDRESS.findall(source_text):
        if address not in everything:
            findings.append(
                f"адрес «{address}» из текста не попал в ролик: без него шаг не выполнить — "
                "покажи его на экране в сцене с этим шагом"
            )
    return findings
