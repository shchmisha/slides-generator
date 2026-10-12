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
