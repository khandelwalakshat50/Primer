from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from svg_fit import parse_viewbox


class OnScreenItem(BaseModel):
    type: Literal["text", "stat", "arrow", "svg"]
    content: str = ""
    emphasis: Literal["high", "medium", "low"] = "medium"
    reveal: float = 0.0
    direction: str = ""
    place: str = ""

    @field_validator("reveal")
    @classmethod
    def _reveal(cls, value: float) -> float:
        return max(0.0, min(0.92, float(value)))

    @field_validator("direction")
    @classmethod
    def _direction(cls, value: str) -> str:
        return value if value in {"up", "down", "left", "right", ""} else ""

    @field_validator("place")
    @classmethod
    def _place(cls, value: str) -> str:
        raw = (value or "").strip().lower()
        aliases = {"up": "top", "down": "bottom"}
        raw = aliases.get(raw, raw)
        return raw if raw in {"top", "bottom", "left", "right", "center", ""} else ""


class ReelVideoModel(BaseModel):
    id: str = ""
    url: str = ""
    model: str = ""
    seconds: int = 8
    size: str = "720x1280"


class ReelImageModel(BaseModel):
    id: str = ""
    url: str = ""
    model: str = ""
    size: str = "1024x1536"
    mime: str = "image/png"


class ReelBeatModel(BaseModel):
    scene_id: str
    purpose: str
    narration: str
    bg: str
    on_screen: list[OnScreenItem] = Field(default_factory=list)
    # The storyboard's plan for this beat. Carried through so the frontend can
    # style by emphasis and so a course can reuse earlier visual language.
    visual: dict = Field(default_factory=dict)
    media: str = "svg"
    video: Optional[ReelVideoModel] = None
    image: Optional[ReelImageModel] = None

    @field_validator("bg")
    @classmethod
    def _bg(cls, value: str) -> str:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value or ""):
            raise ValueError("bg must be a #RRGGBB hex")
        return value.upper()

    @field_validator("media")
    @classmethod
    def _media(cls, value: str) -> str:
        raw = (value or "").strip().lower()
        return raw if raw in {"svg", "sora", "image"} else "svg"


class ReelPlanModel(BaseModel):
    title: str
    hook: ReelBeatModel
    scenes: list[ReelBeatModel]
    payoff: ReelBeatModel
    # Reel-level visual vocabulary from the storyboard.
    world: dict = Field(default_factory=dict)
    # Production telemetry: per-step time, model, tokens, cost.
    metrics: dict = Field(default_factory=dict)
    script_model: str = ""
    svg_model: str = ""
    visual_engine: str = "svg"
    video: Optional[ReelVideoModel] = None

    def beats(self) -> list[ReelBeatModel]:
        return [self.hook, *self.scenes, self.payoff]

    def dump(self) -> dict:
        return self.model_dump()


_STOP = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "your",
    "when",
    "then",
    "than",
    "into",
    "onto",
    "just",
    "more",
    "less",
    "also",
    "have",
    "been",
    "will",
    "would",
    "could",
    "about",
    "what",
    "when",
    "does",
    "idea",
    "thing",
    "stuff",
}


def source_terms(answer: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9%\-]{2,}|\$?\d+(?:\.\d+)?%?", answer.lower())
    return {t for t in tokens if t not in _STOP}


def heading_is_grounded(heading: str, terms: set[str]) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9%\-]{2,}|\$?\d+(?:\.\d+)?%?", heading.lower())
    if not words:
        return False
    for word in words:
        if word in terms:
            return True
        stem = word[:-1] if word.endswith("s") and len(word) > 3 else word
        if stem in terms:
            return True
    return False


_GENERIC = re.compile(
    r"\b(wants|the idea|here's the|the thing|basically|secret|magic|crazy|"
    r"mind.?blown|wait for it|plot twist)\b",
    re.I,
)


def reel_issues(plan: ReelPlanModel, answer: str) -> list[str]:
    issues: list[str] = []
    terms = source_terms(answer)
    beats = plan.beats()
    if not 3 <= len(beats) <= 8:
        issues.append(f"expected 3–8 beats, got {len(beats)}")
    for beat in beats:
        heading = next((item.content for item in beat.on_screen if item.type in {"text", "stat"}), "")
        if not heading:
            issues.append(f"{beat.scene_id}: missing heading")
        else:
            primary = heading.split()
            if len(primary) > 10:
                issues.append(f"{beat.scene_id}: heading over 10 words ({heading!r})")
            if _GENERIC.search(heading):
                issues.append(f"{beat.scene_id}: generic heading ({heading!r})")
            if terms and not heading_is_grounded(heading, terms):
                issues.append(
                    f"{beat.scene_id}: heading {heading!r} does not use a noun/number from the source answer"
                )
        if not beat.narration.strip():
            issues.append(f"{beat.scene_id}: empty narration")
        for item in beat.on_screen:
            if item.type == "svg":
                if not item.content:
                    issues.append(f"{beat.scene_id}: empty svg")
                elif parse_viewbox(item.content) is None:
                    issues.append(f"{beat.scene_id}: svg missing viewBox")
    return issues


def parse_reel_model(raw: dict) -> tuple[ReelPlanModel | None, str]:
    try:
        return ReelPlanModel.model_validate(raw), ""
    except ValidationError as exc:
        return None, str(exc)
