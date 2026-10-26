"""The creative layer: script -> visual story, with no SVG anywhere in sight.

This module exists because asking one model to design a narrative AND author
SVG in the same call makes it optimize for the SVG constraints — they are
concrete and checkable, so they win the model's attention. Here there is
nothing to comply with except the story.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from openai import OpenAI
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError, field_validator

from metrics import Recorder, timed, usage_from_gemini, usage_from_openai

STORYBOARD_SYSTEM = """You are the director of a short explainer film. You get a finished script for one lesson. You do not rewrite the teaching — you decide what the viewer SEES.

Your output is a storyboard: a tiny visual story. Not a list of diagrams.

THINK IN ONE CONTINUOUS VISUAL WORLD
This is the thing that separates a directed film from a slideshow. The reel is ONE world that changes, not a new picture every beat.
Pick the few objects the whole reel will use, then keep using THEM. If beat 1 shows a stack of $100, beat 2 does not show "a compound interest diagram" — it shows THAT SAME STACK, taller. Every beat after the first must say what it KEEPS from the beat before and what CHANGES.
Prefer transformation over replacement. If you find yourself inventing a new picture for a beat, you have probably lost the thread. The CHANGE still has to be obvious at a glance — taller, split, a new piece in accent — not the same frame with a different caption.

THE STORY SHAPE
hook -> establish -> transform -> complication -> revelation -> payoff
Not every reel needs all six, but the feeling should be there:
  "I see it."  ->  "oh, that's changing."  ->  "wait..."  ->  "ahh, that's why."
The hook does not restate the lesson. It puts one concrete thing on screen and opens a small question. "Here's $100." — not "compound interest grows over time."
The payoff is a visual realization, not a summary slide. It should reuse something established earlier and make the relationship visible. The last beat should be SIMPLER than the middle ones: complexity builds toward clarity.

MOTION IS THE MEANING
Choose motion that IS the concept, not decoration. Growth = the thing physically grows. Accumulation = pieces stack. Flow = something travels a path. Feedback = motion returns to where it started. Compounding = the output becomes the next input. Comparison = two things move apart. Scarcity = things disappear.
Test: if removing the motion leaves the same meaning, the motion is not doing enough.

RHYTHM — DO NOT MAKE EVERY BEAT EQUALLY LOUD
Identical beats are monotonous even when each one is good. Vary emphasis deliberately: a quiet setup, then movement, then escalation, then a quiet reveal. Mark each beat quiet / normal / loud, and do not mark them all normal.

THE FRAME IS TALL
Shots are drawn on a tall portrait canvas, not a wide one. Prefer compositions that use vertical space — a stack that grows upward, something falling, a queue running down the screen, a before/after placed one above the other. Side-by-side comparisons still work, but do not describe shots that only make sense as a wide strip.

DRAW THE WORLD
Draw almost every beat. Type "none" at most once. "typography" still sits ON the object (huge $110 in front of the same stack) — it does not replace the world with a blank slide.
The middle of the reel should be the richest pictures: things split, stack, travel, collide. Do not save the drawing for later and leave three beats of heading-only.
A bad diagram is worse than no diagram, but an empty reel is worse than both.

METAPHOR, ONLY WHEN IT IS REAL
Use a physical metaphor when the concept genuinely maps to one: a queue is people in a line, a bottleneck is a narrow pipe, leverage is a seesaw, compounding is a snowball, liquidity is water.
Never use stock-icon shorthand: no lightbulb for an idea, no rocket for growth, no brain for thinking, no gears for a system. If the metaphor did not come out of the actual mechanism, do not use one.

WHERE THIS REEL SITS IN THE COURSE
You are told whether this is the first reel, the last, a middle one, or standalone. A course is ONE YouTube film cut into episodes.
- FIRST (or standalone): the hook's narration begins with the script's framing line — the one sentence naming what we are about to figure out — and then gets on with showing the concrete thing. It is the first breath of the hook, spoken over the first image. Do NOT give it its own beat and do NOT make it a title card; a beat whose whole content is the topic name is the dullest possible opening.
- LAST (or standalone): the payoff's narration ends on the script's converging line — what it all came down to. The payoff visual stays the simplest frame in the reel; the words carry the conclusion. Keep the same objects the series has been using.
- MIDDLE: this is the next episode, not a new video. The hook continues the previous episode (you are given what they last heard and saw). Same objects, next cause. Do not open with a new metaphor, a definition, or a title card. End on this chapter's step — the next episode will pick it up.

If earlier episodes are provided, REUSE that visual world. The first beat should look like the last beat of the previous film, then change.

HEADINGS ARE ON-SCREEN COPY
A heading is a chyron a human editor would actually superimpose: it names the thing on screen — a term, a number, an object from the script. 2-6 words.
At least one word must come from the script (same stem counts). Never: "X wants Y", "the idea", "the secret", "wait for it", "the real reason", or any heading you could paste onto a different topic.

ACCENT MEANS ONE THING
State what the accent color means for this reel — the changing quantity, the new arrival, the consequence — and it means only that. If everything is emphasized, nothing is.

OUTPUT — JSON only, no markdown, no commentary:
{
  "title": "3-5 word title using a word from the script",
  "world": {
    "objects": [
      {"id": "short_snake_case", "what": "what it is, in plain words", "how_drawn": "the simplest shape that reads on a phone"}
    ],
    "anchor": "what stays put across beats, and where it sits",
    "accent_means": "the one thing accent color marks in this reel"
  },
  "beats": [
    {
      "role": "hook | establish | transform | complication | revelation | payoff",
      "heading": "2-6 words, real on-screen copy",
      "narration": "1-2 short spoken sentences",
      "visual": {
        "type": "object | transformation | comparison | accumulation | split | merge | flow | before_after | sequence | timeline | graph | network | counter | reveal | metaphor | typography | minimal | none",
        "sees": "what is on screen, plainly, as a shot description",
        "keeps": "what survives from the previous beat (empty for the first beat)",
        "changes": "what is different from the previous beat",
        "focus": "where the eye should go",
        "motion": "what moves, and what that movement means",
        "emphasis": "quiet | normal | loud"
      }
    }
  ]
}

WORKED EXAMPLE (compound interest) — note how one object carries the whole reel:
world: objects = [money_stack: "a stack of $100 in flat bills", "flat rectangles stacked upward, centered"], anchor = "the stack stays centered, growing upward", accent_means = "interest that was just added"
1 hook        typography      sees "$100 alone on screen"                    keeps ""            changes "-"                          loud
2 establish   object          sees "the $100 as a small stack of bills"      keeps "the number"  changes "the number becomes a thing"  normal
3 transform   accumulation    sees "one accent bill slides on top: $110"     keeps "the stack"   changes "a bill is added, in accent"  normal
4 revelation  merge           sees "the accent bill turns ink-colored, now part of the stack" keeps "the same stack" changes "the new bill stops being separate" loud
5 payoff      typography      sees "$121, the stack faint behind it"         keeps "the stack"   changes "the next gain is bigger"     quiet
Five beats, one object, each beat changing it. That is the standard.
"""

STORYBOARD_CRITIC_SYSTEM = """You are a tough but fair creative editor for short explainer films. You get a storyboard and a list of problems a checker already found. You do not care about SVG, syntax, or technical validity — only whether this is a directed film or a decorated list.

Reject and fix a storyboard that:
- reads as "sentence -> diagram -> sentence -> diagram" with no through-line
- invents an unrelated visual for each beat instead of transforming one world
- has no real payoff, or a payoff that just restates the lesson
- marks every beat the same emphasis, or makes every beat the same visual type
- draws a generic unrelated diagram instead of the world that was established
- uses a stock-icon metaphor (lightbulb, rocket, brain, gears, lock)
- has a hook that states the conclusion instead of opening a question
- would play as a standalone video that ignores the previous episode (new metaphor, cold start)
- empties the middle of the reel (too many none/typography-without-object beats)
- copies the same `sees` from beat to beat with only the caption changing

Fix by the smallest edit that works: change a beat's visual type, rewrite `keeps`/`changes` so the world actually carries forward AND visibly changes, drop a pointless visual, re-balance emphasis, or simplify the payoff. Keep the teaching, the narration's facts, and the beat count unless a beat truly must go. Do not "fix" density by deleting drawings.

OUTPUT — JSON only:
{
  "approved": true or false,
  "storyboard": { ...the full storyboard, revised if needed, identical if approved... },
  "changes": ["short note on what changed and why", ...]
}
If it is already strong, set approved true, return it unchanged, and leave changes empty.
"""

VISUAL_TYPES = {
    "object",
    "transformation",
    "comparison",
    "accumulation",
    "split",
    "merge",
    "flow",
    "before_after",
    "sequence",
    "timeline",
    "graph",
    "network",
    "counter",
    "reveal",
    "metaphor",
    "typography",
    "minimal",
    "none",
}

ROLES = {"hook", "establish", "transform", "complication", "revelation", "payoff", "build"}
EMPHASIS = {"quiet", "normal", "loud"}
MEDIA_KINDS = {"svg", "sora", "image"}
PLACES = {"top", "bottom", "left", "right", "center"}
PLACE_ALIASES = {
    "up": "top",
    "north": "top",
    "down": "bottom",
    "south": "bottom",
    "west": "left",
    "east": "right",
    "middle": "center",
    "centre": "center",
}
MOTION_TYPES = {
    "transformation",
    "flow",
    "accumulation",
    "merge",
    "split",
    "reveal",
    "sequence",
}
SVG_BEST_TYPES = {"graph", "network", "timeline", "comparison", "typography", "counter"}
IMAGE_TYPES = {"object", "metaphor", "minimal", "before_after", "none"}
OVERLAY_PLACES = ["top", "bottom", "left", "right"]


def normalize_place(value: str) -> str:
    raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    raw = PLACE_ALIASES.get(raw, raw)
    return raw if raw in PLACES else "top"


# Types that carry the beat without a drawn diagram.
NO_DIAGRAM_TYPES = {"none"}
TYPOGRAPHY_TYPES = {"typography", "counter"}


class OverlayLine(BaseModel):
    text: str = ""
    place: str = "top"
    emphasis: str = "high"

    @field_validator("place")
    @classmethod
    def _place(cls, value: str) -> str:
        return normalize_place(value)

    @field_validator("emphasis")
    @classmethod
    def _emphasis(cls, value: str) -> str:
        value = (value or "").strip().lower()
        return value if value in {"high", "medium", "low"} else "high"


class VisualPlan(BaseModel):
    type: str = "object"
    sees: str = ""
    keeps: str = ""
    changes: str = ""
    focus: str = ""
    motion: str = ""
    emphasis: str = "normal"
    medium: str = "svg"

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        value = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
        return value if value in VISUAL_TYPES else "object"

    @field_validator("emphasis")
    @classmethod
    def _emphasis(cls, value: str) -> str:
        value = (value or "").strip().lower()
        return value if value in EMPHASIS else "normal"

    @field_validator("medium")
    @classmethod
    def _medium(cls, value: str) -> str:
        value = (value or "").strip().lower()
        return value if value in MEDIA_KINDS else "svg"

    def wants_diagram(self) -> bool:
        return self.type not in NO_DIAGRAM_TYPES

    def is_typographic(self) -> bool:
        return self.type in TYPOGRAPHY_TYPES


class WorldObject(BaseModel):
    id: str = ""
    what: str = ""
    how_drawn: str = ""


class VisualWorld(BaseModel):
    objects: list[WorldObject] = Field(default_factory=list)
    anchor: str = ""
    accent_means: str = ""

    def brief(self) -> str:
        """Compact description handed to the renderer and to later lessons."""
        lines = []
        for obj in self.objects[:5]:
            label = obj.id or obj.what
            lines.append(f"- {label}: {obj.what} — drawn as {obj.how_drawn}")
        if self.anchor:
            lines.append(f"anchor: {self.anchor}")
        if self.accent_means:
            lines.append(f"accent means: {self.accent_means}")
        return "\n".join(lines) or "(no shared objects declared)"


class StoryBeat(BaseModel):
    role: str = "build"
    heading: str = ""
    narration: str = ""
    visual: VisualPlan = Field(default_factory=VisualPlan)
    overlays: list[OverlayLine] = Field(default_factory=list)

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        value = (value or "").strip().lower()
        return value if value in ROLES else "build"


class Storyboard(BaseModel):
    title: str = ""
    world: VisualWorld = Field(default_factory=VisualWorld)
    beats: list[StoryBeat] = Field(default_factory=list)

    def dump(self) -> dict:
        return self.model_dump()


def _clip_phrase(text: str, max_words: int = 8) -> str:
    words = re.findall(r"[A-Za-z0-9$%]+(?:[-'][A-Za-z0-9$%]+)?", (text or "").strip())
    return " ".join(words[:max_words]).strip()


def _unique_phrase(candidate: str, taken: list[str]) -> bool:
    needle = re.sub(r"\s+", " ", candidate.lower()).strip()
    if len(needle) < 4:
        return False
    return all(needle not in t and t not in needle for t in taken)


def fill_photo_overlays(beat: StoryBeat) -> None:
    """Sora and stills need more on-screen copy than a single chyron, placed
    around the frame. SVG beats keep their existing in-diagram type."""
    lines = [o for o in beat.overlays if o.text.strip()]
    heading = _clip_phrase(beat.heading, 6) or _clip_phrase(beat.narration, 6)
    taken = [re.sub(r"\s+", " ", o.text.lower()) for o in lines]
    if heading and _unique_phrase(heading, taken):
        lines.insert(0, OverlayLine(text=heading, place="top", emphasis="high"))
        taken.insert(0, heading.lower())
    extras: list[str] = []
    for src, n in (
        (beat.visual.focus, 6),
        (beat.visual.changes, 8),
        (beat.visual.sees, 8),
        (beat.narration, 10),
    ):
        phrase = _clip_phrase(src, n)
        if phrase and _unique_phrase(phrase, taken + extras):
            extras.append(phrase)
    while len(lines) < 3 and extras:
        text = extras.pop(0)
        lines.append(OverlayLine(text=text, place="bottom", emphasis="medium"))
        taken.append(text.lower())
    used: set[str] = set()
    for i, line in enumerate(lines[:4]):
        if line.place not in PLACES or line.place in used:
            line.place = OVERLAY_PLACES[i % len(OVERLAY_PLACES)]
        used.add(line.place)
    beat.overlays = lines[:4]


def assign_mixed_media(board: Storyboard) -> None:
    """Cap mixed reels at 1 Sora clip and 2 stills; everything else stays SVG.

    The opening beat is always Sora so the reel starts on video.
    """
    beats = board.beats
    n = len(beats)
    if n < 3:
        for beat in beats:
            beat.visual.medium = "svg"
        return

    for beat in beats:
        beat.visual.medium = "svg"
    beats[0].visual.medium = "sora"

    image_scores: list[tuple[int, int]] = []
    for i, beat in enumerate(beats):
        if i == 0:
            continue
        score = 0
        if beat.visual.type in IMAGE_TYPES:
            score += 3
        if beat.visual.type in SVG_BEST_TYPES:
            score -= 5
        if beat.role in {"establish", "payoff"}:
            score += 1
        image_scores.append((score, i))
    image_scores.sort(reverse=True)
    for _, i in image_scores[:2]:
        beats[i].visual.medium = "image"

    for beat in beats:
        if beat.visual.medium in {"sora", "image"}:
            fill_photo_overlays(beat)


def storyboard_issues(board: Storyboard) -> list[str]:
    """Cheap deterministic checks. These decide whether paying for the critic
    is worth it — a storyboard that passes all of them is left alone."""
    issues: list[str] = []
    beats = board.beats
    if len(beats) < 3:
        issues.append(f"only {len(beats)} beats; a story needs at least 3")
        return issues

    if not board.world.objects:
        issues.append("no shared visual objects declared — beats will drift into unrelated pictures")
    if not board.world.anchor:
        issues.append("no anchor: nothing is declared to stay put across beats")

    # Continuity: later beats must carry something forward.
    missing_continuity = [
        b.role for i, b in enumerate(beats) if i > 0 and not b.visual.keeps.strip()
    ]
    if len(missing_continuity) > max(1, len(beats) // 4):
        issues.append(
            f"{len(missing_continuity)} beats say nothing about what they keep from the previous beat"
        )
    no_change = [i for i, b in enumerate(beats) if i > 0 and not b.visual.changes.strip()]
    if no_change:
        issues.append(f"{len(no_change)} beats do not say what changed — nothing is transforming")

    # Monotony: identical type or identical emphasis across the reel.
    types = [b.visual.type for b in beats]
    if len(set(types)) <= 1:
        issues.append(f"every beat is visual type '{types[0]}' — no visual rhythm")
    emphases = [b.visual.emphasis for b in beats]
    if len(set(emphases)) <= 1:
        issues.append("every beat has the same emphasis — nothing stands out")

    # Payoff must exist and should not be the busiest beat.
    roles = [b.role for b in beats]
    if "payoff" not in roles:
        issues.append("no payoff beat — the reel has no earned ending")
    if "hook" not in roles:
        issues.append("no hook beat")

    # Sparse reels and cloned shots, not "too many diagrams".
    emptyish = [b for b in beats if b.visual.type == "none"]
    if len(emptyish) > 1:
        issues.append("more than one empty beat — most beats should show the world changing")
    sees = [
        re.sub(r"\s+", " ", (b.visual.sees or "").lower()).strip()[:90]
        for b in beats
        if b.visual.sees.strip()
    ]
    if len(sees) >= 3 and len(set(sees)) <= 2:
        issues.append("shots repeat the same picture — later beats must visibly change the world")

    return issues


def _parse_json(text: str) -> dict:
    blob = (text or "").strip()
    blob = re.sub(r"^```(?:json)?\s*", "", blob)
    blob = re.sub(r"\s*```$", "", blob)
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _coerce(raw: dict) -> Optional[Storyboard]:
    if not raw:
        return None
    beats_in = raw.get("beats")
    if not isinstance(beats_in, list) or len(beats_in) < 3:
        return None
    try:
        board = Storyboard.model_validate(
            {
                "title": str(raw.get("title") or "").strip()[:80],
                "world": raw.get("world") or {},
                "beats": beats_in[:9],
            }
        )
    except ValidationError:
        return None
    board.beats = [b for b in board.beats if b.narration.strip() or b.heading.strip()]
    if len(board.beats) < 3:
        return None
    # The first beat cannot inherit anything, and the last beat is the payoff
    # whether or not the model labeled it.
    board.beats[0].visual.keeps = ""
    if not any(b.role == "payoff" for b in board.beats):
        board.beats[-1].role = "payoff"
    if not any(b.role == "hook" for b in board.beats):
        board.beats[0].role = "hook"
    return board


def _complete_json(
    system: str,
    user: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    recorder: Recorder | None = None,
    step: str = "storyboard",
    step_label: str = "Storyboard",
) -> dict:
    import llm

    text, _label = llm.complete(
        system,
        user,
        json_mode=True,
        openai_key_value=openai_key,
        gemini_key_value=gemini_key,
        openai_model_name=openai_model,
        gemini_model_name=gemini_model,
        recorder=recorder,
        step=step,
        step_label=step_label,
    )
    return _parse_json(text)


_POSITION_BRIEF = {
    "first": (
        "POSITION: FIRST reel of the course. The hook's narration opens with the script's framing "
        "line (what we are about to figure out), then immediately shows the concrete thing. Not a "
        "title card, not its own beat."
    ),
    "last": (
        "POSITION: LAST reel of the course. The payoff's narration ends on the script's converging "
        "line — what it all came down to — over the simplest frame in the reel."
    ),
    "only": (
        "POSITION: STANDALONE. The hook opens with the script's framing line, and the payoff ends "
        "on its converging line. Both are single sentences riding on top of real images."
    ),
    "middle": (
        "POSITION: MIDDLE episode of one film. The hook continues the previous episode — same "
        "object, next cause. You are given what they last heard and saw; start there. No new "
        "metaphor, no cold-start definition, no title card. End on this chapter's step."
    ),
}


def _storyboard_prompt(
    question: str, title: str, script: str, prior_world: str = "", position: str = "middle"
) -> str:
    parts = [
        f"Lesson title: {title.strip() or 'Untitled'}",
        f"What the learner asked: {question.strip() or '(topic lesson)'}",
        f"The script to direct (do not add facts it does not contain):\n{script.strip()[:5000]}",
        _POSITION_BRIEF.get(position, _POSITION_BRIEF["middle"]),
    ]
    if prior_world.strip():
        parts.append(
            "PREVIOUS EPISODES — the viewer already watched these. This reel is the next "
            "chapter of that same film. Reuse the objects. Hook picks up from the last state. "
            "Do not start a new picture language.\n"
            f"{prior_world.strip()[:4500]}"
        )
    parts.append("Direct it. One world, carried forward beat to beat, with an earned payoff.")
    return "\n\n".join(parts)


STORYBOARD_OUTPUT_REMINDER = """
OUTPUT — JSON only, no markdown, no commentary. You MUST still emit this schema:
{
  "title": "3-5 word title using a word from the script",
  "world": {
    "objects": [{"id": "short_snake_case", "what": "what it is", "how_drawn": "simplest phone-readable shape"}],
    "anchor": "what stays put across beats",
    "accent_means": "the one thing accent color marks"
  },
  "beats": [
    {
      "role": "hook | establish | transform | complication | revelation | payoff",
      "heading": "2-6 words",
      "narration": "1-2 short spoken sentences",
      "visual": {
        "type": "object | transformation | comparison | accumulation | split | merge | flow | before_after | sequence | timeline | graph | network | counter | reveal | metaphor | typography | minimal | none",
        "sees": "what is on screen",
        "keeps": "what survives from the previous beat",
        "changes": "what is different",
        "focus": "where the eye should go",
        "motion": "what moves, and what that means",
        "emphasis": "quiet | normal | loud",
        "medium": "svg | sora | image  (optional; mixed-media reels only)"
      },
      "overlays": [
        {"text": "on-screen line", "place": "top | bottom | left | right | center", "emphasis": "high | medium | low"}
      ]
    }
  ]
}
"""

MIXED_MEDIA_ADDENDUM = """
MIXED MEDIA — this reel is NOT all SVG and NOT one looping video.

You have a strict budget:
- The FIRST beat is always medium "sora" (generated video). Open on motion. Design that hook as a shot that moves.
- exactly TWO later beats are medium "image" (photoreal stills). Use them for a concrete object, a before/after freeze, or the payoff.
- EVERY OTHER beat is medium "svg". SVG is best for graphs, comparisons, networks, timelines, counters, and big type with labels. Do not waste stills on those. Never put sora on a beat after the hook.

Do not put captions, numbers, or labels inside the Sora or image descriptions — Primer overlays type on top.
Never describe people, faces, crowds, or named public figures in sora/image shots. Historical topics are told with objects (paper tearing, a line on a map, two stacks pulling apart), not likenesses.

For sora and image beats, fill overlays with 3–4 short lines of on-screen copy (more than a heading). Place them around the frame: top, bottom, left, right — not all stacked in the same spot. Heading stays 2–6 words; extra overlay lines can be up to ~10 words, still grounded in the script.

For svg beats, overlays may be empty; the heading plus the diagram carry the beat.
"""


def effective_storyboard_system(director_system: str = "", mixed: bool = False) -> str:
    text = (director_system or "").strip() or STORYBOARD_SYSTEM
    if '"beats"' in text and "world" in text:
        system = text
    else:
        system = text.rstrip() + "\n" + STORYBOARD_OUTPUT_REMINDER
    if mixed:
        system = system.rstrip() + "\n" + MIXED_MEDIA_ADDENDUM
    return system


def plan_storyboard(
    question: str,
    title: str,
    script: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    prior_world: str = "",
    director_system: str = "",
    position: str = "middle",
    recorder: Recorder | None = None,
    mixed: bool = False,
) -> Optional[Storyboard]:
    system = effective_storyboard_system(director_system, mixed=mixed)
    user = _storyboard_prompt(question, title, script, prior_world, position)
    board = _coerce(_complete_json(system, user, openai_key, gemini_key, openai_model, gemini_model, recorder))
    if board:
        return board
    retry = user + (
        "\n\nYour last storyboard was unusable. Return JSON with at least 3 beats, each with "
        "heading, narration, and a visual object containing type/sees/keeps/changes/focus/"
        "motion/emphasis, plus a world with objects, anchor and accent_means."
    )
    return _coerce(_complete_json(system, retry, openai_key, gemini_key, openai_model, gemini_model,
                                  recorder, step="storyboard_retry", step_label="Storyboard retry"))


def critique_storyboard(
    board: Storyboard,
    issues: list[str],
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    recorder: Recorder | None = None,
) -> tuple[Storyboard, list[str]]:
    user = (
        f"Storyboard:\n{json.dumps(board.dump(), ensure_ascii=False)[:9000]}\n\n"
        "Problems a checker already found:\n"
        + ("\n".join(f"- {row}" for row in issues) or "- none listed")
        + "\n\nFix what is genuinely weak. Return the full storyboard."
    )
    data = _complete_json(
        STORYBOARD_CRITIC_SYSTEM, user, openai_key, gemini_key, openai_model, gemini_model,
        recorder, step="storyboard_critic", step_label="Creative critique",
    )
    revised = _coerce(data.get("storyboard") if isinstance(data.get("storyboard"), dict) else {})
    changes = [str(c).strip() for c in (data.get("changes") or []) if str(c).strip()]
    if not revised:
        return board, []
    return revised, changes
