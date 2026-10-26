"""Keep Sora and image prompts off OpenAI's people / public-figure tripwires."""

from __future__ import annotations

import re

SAFETY_RULES = (
    "- No people, bodies, faces, crowds, silhouettes, or hands.\n"
    "- No public figures, politicians, celebrities, or named historical persons — "
    "even if the lesson is about them. Show objects, paper, architecture, maps as props, "
    "light, and land — never a likeness.\n"
    "- No talking heads. No logos."
)

_PEOPLE = re.compile(
    r"\b("
    r"people|person|persons|human|humans|man|men|woman|women|child|children|"
    r"crowd|crowds|face|faces|portrait|portraits|celebrity|politician|leader|"
    r"soldier|soldiers|refugee|refugees|crowd|mob|audience|speaker|"
    r"gandhi|nehru|jinnah|hitler|stalin|president|prime minister"
    r")\b",
    re.I,
)


def is_moderation_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("moderation", "safety system", "public-figure", "blocked by our moderation")
    )


def scrub_people(text: str) -> str:
    cleaned = _PEOPLE.sub("object", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def abstract_object_shot(shot: str) -> str:
    idea = scrub_people(shot)[:180] or "two sheets of paper slowly pull apart"
    return (
        "Quiet studio tabletop. Only inanimate objects: paper, ink, wood blocks, string, light. "
        "No humans and no likeness of any person. "
        f"Object motion that stands for the idea: {idea}."
    )
