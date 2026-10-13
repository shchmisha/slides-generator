"""Video scenario: scenes for a 60–75 s explainer, every fact taken from the source text.

The model writes narration and picks a visual type per scene; HTML is never written by the
model (scenes are rendered from our own HyperFrames templates).
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ShortText = Annotated[str, Field(min_length=1, max_length=90)]
Narration = Annotated[str, Field(min_length=1, max_length=420)]

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


def fit_duration(
    scenario: VideoScenario,
    min_total_s: float = MIN_TOTAL_S,
    max_total_s: float = MAX_TOTAL_S,
) -> tuple[VideoScenario, str | None]:
    """Scale scene durations into the allowed total when the model's sum is off (it often
    lands at 78–80 s for a 60–75 s limit). Code adds numbers better than a language model.
    Shortening is done only while every scene stays speakable; otherwise the check reports
    it and the model shortens the text."""
    total = sum(scene.duration_s for scene in scenario.scenes)
    if min_total_s <= total <= max_total_s or total <= 0:
        return scenario, None
    target = max_total_s if total > max_total_s else min_total_s
    factor = target / total
    scenes = []
    for scene in scenario.scenes:
        seconds = round(scene.duration_s * factor, 1)
        if seconds < 3 or len(scene.narration.split()) > seconds * MAX_WORDS_PER_SECOND:
            return scenario, None
        scenes.append(scene.model_copy(update={"duration_s": seconds}))
    # Rounding can leave the sum a tenth outside the window: settle it on the longest scene.
    drift = round(target - sum(s.duration_s for s in scenes), 1)
    if drift:
        longest = max(range(len(scenes)), key=lambda i: scenes[i].duration_s)
        scenes[longest] = scenes[longest].model_copy(
            update={"duration_s": round(scenes[longest].duration_s + drift, 1)}
        )
    fitted = scenario.model_copy(update={"scenes": scenes})
    return fitted, f"Длительность сцен подогнана кодом: {total:g} → {target:g} с"


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
