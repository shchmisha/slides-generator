"""What the user chooses in the form; the prompts and the checks follow these settings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["ru", "en"]
Tone = Literal["business", "neutral", "friendly"]

LANGUAGE_NAMES: dict[Language, str] = {"ru": "русский", "en": "английский"}
TONE_NAMES: dict[Tone, str] = {
    "business": "деловой и спокойный",
    "neutral": "нейтральный, информационный",
    "friendly": "дружелюбный, простыми словами",
}


class DeckSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slides: int | None = Field(default=None, ge=3, le=15)  # None: as the materials ask, else 6–10
    language: Language = "ru"
    tone: Tone = "business"


class VideoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_seconds: int = Field(default=60, ge=20, le=300)
    max_seconds: int = Field(default=75, ge=20, le=300)
    language: Language = "ru"
    tone: Tone = "friendly"
