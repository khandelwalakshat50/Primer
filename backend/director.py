from __future__ import annotations

import json
import logging
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from openai import OpenAI
from google import genai
from google.genai import types

from svg_agent import extract_svg, illustrate_reel, render_scene
import audio_cache
from metrics import Recorder, timed
from reel_schema import parse_reel_model, reel_issues
from storyboard import (
    OverlayLine,
    Storyboard,
    assign_mixed_media,
    critique_storyboard,
    plan_storyboard,
    storyboard_issues,
)

logger = logging.getLogger("primer.director")

# (stage, human label, fraction 0..1 of the director's own work)
ProgressFn = Callable[[str, str, float], None]

# Shots after the opening one are drawn together. Drawing them one by one (each
# waiting on the last) is what made a single lesson take 5-10 minutes.
DRAW_WORKERS = 4


def _report(on_progress: Optional[ProgressFn], stage: str, label: str, fraction: float) -> None:
    if not on_progress:
        return
    try:
        on_progress(stage, label, max(0.0, min(1.0, fraction)))
    except Exception:
        logger.debug("progress callback failed", exc_info=True)

# Locked palettes: one ink for type/strokes, several distinct stage papers,
# saturated fills for SVG bodies, one accent for the thing that changes.
# A reel picks one set and keeps it — never re-randomized mid-reel.
PALETTE_SETS = [
    {
        "ink": "#1C1917",
        "bg": ["#FFF4C8", "#FFD9B8", "#C8F0E0", "#FFD0D0"],
        "fills": ["#F4B942", "#2A9D8F", "#F07A4A"],
        "accent": "#E23B2A",
    },
    {
        "ink": "#0F2744",
        "bg": ["#C8EBFF", "#FFE7B8", "#D4C8FF", "#C8F5E8"],
        "fills": ["#2E86AB", "#F6C445", "#7C5CBF"],
        "accent": "#F25C05",
    },
    {
        "ink": "#1A1028",
        "bg": ["#EDD6FF", "#FFD6EA", "#D8F7C8", "#FFE9A8"],
        "fills": ["#8B5CF6", "#F472B6", "#3D9B54"],
        "accent": "#E11D48",
    },
    {
        "ink": "#1F140F",
        "bg": ["#FFE0CC", "#FFF0A8", "#BFE8E0", "#FFC8C8"],
        "fills": ["#EF5B3D", "#2A9D8F", "#E9C46A"],
        "accent": "#C1121F",
    },
    {
        "ink": "#14261C",
        "bg": ["#D4F0B8", "#FFE6A0", "#C4E4FF", "#FFD0B8"],
        "fills": ["#3D9B54", "#F4A261", "#2E86AB"],
        "accent": "#E63946",
    },
    {
        "ink": "#111827",
        "bg": ["#C8D8FF", "#FFE6B0", "#F5C8F8", "#B8F3F3"],
        "fills": ["#2563EB", "#F59E0B", "#DB2777"],
        "accent": "#DC2626",
    },
]


def pick_palette() -> dict:
    return random.choice(PALETTE_SETS)

DIRECTOR_SYSTEM = """You are an elite EXPLAINER VIDEO director — the Vox / 3Blue1Brown kind. You receive a finished script for ONE lesson. Do NOT re-answer or re-teach it. Cut it into beats.

You are directing for someone learning SLOWLY on a phone, who knows nothing about this topic and hears the narration once. Your whole job is to make one idea land.

ONE STEP PER BEAT
Each beat shows exactly ONE step of the explanation. Not a summary, not two things at once. If a beat's narration contains "and" joining two ideas, split it into two beats.
Beats are cheap. Use 5 to 7 of them if the script has that many steps. A beat that shows one small step clearly is always better than a beat that races through three.

SHOW, DON'T ANNOUNCE
The diagram carries the teaching; the heading only labels what is on screen. Never use a beat to announce what is coming ("first, the basics") or to summarize what happened. Every beat advances the actual explanation by one visible step.
Build the same picture forward where you can: if the script walks through an example, let consecutive beats show that example progressing, not five unrelated illustrations.

PACING IS TIED TO SPEECH
Each cut lasts exactly as long as that beat's narration, then we cut. Do not pad beats to let them "land" and do not write filler sentences to fill time. Slow pacing comes from SMALL STEPS, never from dead air.

STRUCTURE: hook → 3 to 5 build beats → payoff.

LAYOUT OF EVERY BEAT
1. One heading. 2 to 6 words.
2. Then a diagram (type "svg"): one picture of this single step.
3. Optional: one small label or arrow. Never a second paragraph of text.

HEADINGS MUST BE REAL ON-SCREEN COPY
A heading is a chyron a human editor would actually superimpose. It names the thing in the answer — a term, a number, an object — not a vibe.

Rule: at least one word in the heading must appear in the source answer (same stem counts: bond/bonds, density/dense). Do not invent a metaphor that the answer never used.

GOOD (grounded in the actual subject)
- Ice is 9% less dense          (answer talked about ice density / 9%)
- Old bond vs new bond          (answer compared existing vs newly issued bonds)
- Hydrogen bonds open the lattice (answer mentioned hydrogen bonds and the crystal)
- Price falls as yield rises    (answer used price and yield)

BAD (generic / robotic / unmoored)
- Heat wants out                (personification; "wants" is filler unless the answer is about heat)
- The hidden trick
- Nature's little secret
- Here's the weird part
- Matter finds a way
- The real reason
- Ice is emptier                (poetic paraphrase — say density, not emptiness, unless the answer said empty)

BANNED HEADING PATTERNS
- "X wants Y" / "X hates Y" / "X knows Y"
- "the idea", "the thing", "the secret", "the magic"
- "wait for it", "plot twist", "mind blown"
- Abstract adjectives with no noun from the source (beautiful, crazy, hidden, real)
- A heading you could reuse on a totally different topic

NARRATION
1 or 2 short spoken sentences per beat — one clause each where you can. Use the same nouns as the heading. The cut ends when you stop talking.
Keep the script's own words and its concrete example. Do not add facts the script did not say, and do not swap its specific numbers or objects for abstractions.

SVG
Inline SVG in "content". REQUIRED viewBox="0 0 360 440". All shapes and labels inside x:20-340, y:20-420.
Ink color __INK__. Fill the tall frame — empty top and bottom is a failed shot.
Prefer 8 to 12 <g class="r-el"> groups on a normal beat, each ONE idea (one arrow, one box, one icon, one label). Never merge everything into one group or fuse shapes into one complex path — the frontend reveals these groups one at a time in DOM order.
Order groups so the beat builds: box, then arrow that points, then label that pops. Include at least 2 arrows and 4 labels on a normal beat.
NEVER overlap — overlapping type or stacked boxes looks unprofessional and is a failed shot. Labels sit in empty sky beside or below objects (never on a fill). Boxes do not share pixels; connect them with an arrow across a gap. Arrows may kiss a box edge but must not run through text. Scatter across the tall frame (top, middle, AND bottom, or left/right columns) instead of piling on (180, 220).
Lines, arrows, and circles are STROKE shapes: fill="none" stroke="__INK__" stroke-width="3". An arrow is a line/polyline shaft plus a short polyline chevron head — never a filled polygon arrowhead. Boxes and icons are FILLED shapes (rect/circle using __FILL1__, __FILL2__, __FILL3__, or __ACCENT__). Each shape, stroke or filled, still gets its own <g class="r-el">.
LABELS: 1–4 words / up to 22 characters, font-size 13-18, each label in its own group so it can pop after the arrow. Put font-size and text-anchor on the <text> element itself, and keep every label's full width inside x:20-340.

COLOR IS LOCKED FOR THE WHOLE REEL
This reel's palette is fixed before you write a single beat. Use these colors, no others:
- Ink (strokes, text): __INK__
- Stage papers (beat "bg" only): __BG1__, __BG2__, __BG3__, __BG4__
- Shape fills (SVG bodies): __FILL1__, __FILL2__, __FILL3__
- Accent (the changing thing): __ACCENT__
Every beat's "bg" must be one of the four stage papers. Change paper when the role or emphasis changes, and at least every other beat — do not sit on one wash for the whole reel. Do not invent a fifth background, do not use black or navy as a background.
SVG objects should look colored, not like pale paper cutouts. Give different objects different fills. Accent is for the one thing that changed.

JSON only:
{
  "title": "3-5 word title using a source noun",
  "hook": {
    "scene_id": "hook",
    "purpose": "hook",
    "narration": "...",
    "bg": "__BG1__",
    "on_screen": [
      {"type": "text", "content": "Ice is 9% less dense", "emphasis": "high", "reveal": 0.0, "direction": ""},
      {"type": "svg", "content": "<svg viewBox=\\"0 0 360 440\\">...</svg>", "emphasis": "medium", "reveal": 0.25, "direction": ""}
    ]
  },
  "scenes": [ { "purpose": "build", ... } ],
  "payoff": { "scene_id": "payoff", "purpose": "payoff", ... }
}
"""


def _render_director_system(palette: dict) -> str:
    fills = palette.get("fills") or palette["bg"]
    bgs = palette["bg"]
    text = DIRECTOR_SYSTEM.replace("__INK__", palette["ink"]).replace("__ACCENT__", palette["accent"])
    for i in range(4):
        text = text.replace(f"__BG{i + 1}__", bgs[i] if i < len(bgs) else bgs[i % len(bgs)])
    for i in range(3):
        text = text.replace(f"__FILL{i + 1}__", fills[i] if i < len(fills) else fills[i % len(fills)])
    return text

CRITIQUE_SYSTEM = """You are a short-form editor. You get a source answer and a draft reel JSON.

Revise title, headings, and narration only. Keep the same beats, scene_id, purpose, bg, and SVG markup.

Rubric (fail → rewrite that field):
1. Concrete: heading names a thing, not a mood.
2. Grounded: heading contains a noun, number, or term from the source answer.
3. Short: heading ≤ 6 words. Narration 1–3 sentences.
4. Not generic: no "wants", "the idea", "secret", "here's the", "the real reason", personification.

Return the full reel JSON only.
"""

_FILLER = re.compile(
    r"\b(basically|the idea|this means|important|concept|let'?s|in other words|how it works|explanation|overview)\b",
    re.I,
)


def _strong_copy(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    if _FILLER.search(cleaned):
        cleaned = _FILLER.sub("", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    words = cleaned.split()
    if len(words) > 6:
        cleaned = " ".join(words[:6])
    if len(cleaned) > 42:
        cleaned = cleaned[:42].rsplit(" ", 1)[0]
    return cleaned


def _norm_item(raw: object, index: int) -> dict | None:
    if not isinstance(raw, dict):
        if isinstance(raw, str) and raw.strip():
            content = _strong_copy(raw)
            if not content:
                return None
            return {
                "type": "text",
                "content": content,
                "emphasis": "high" if index == 0 else "medium",
                "reveal": min(0.85, index * 0.2),
                "direction": "",
            }
        return None
    kind = str(raw.get("type") or "text").lower()
    if kind not in {"text", "stat", "arrow", "svg", "label", "diagram"}:
        kind = "text"
    if kind in {"label", "diagram"}:
        kind = "text" if kind == "label" else "svg"
    content = str(raw.get("content") or raw.get("text") or "").strip()
    if kind == "svg":
        content = extract_svg(content, require_viewbox=True)
    elif kind != "arrow":
        content = _strong_copy(content)
    if kind != "arrow" and not content:
        return None
    emphasis = str(raw.get("emphasis") or "medium").lower()
    if emphasis not in {"high", "medium", "low"}:
        emphasis = "medium"
    if index == 0 and kind in {"text", "stat"}:
        emphasis = "high"
    try:
        reveal = float(raw.get("reveal", 0.0 if index == 0 else 0.28))
    except (TypeError, ValueError):
        reveal = 0.0 if index == 0 else 0.28
    reveal = max(0.0, min(0.92, reveal))
    direction = str(raw.get("direction") or "").lower()
    if direction not in {"up", "down", "left", "right", ""}:
        direction = ""
    return {
        "type": kind,
        "content": content,
        "emphasis": emphasis,
        "reveal": reveal,
        "direction": direction,
    }


def _norm_bg(raw: object, index: int, previous: str, bg_choices: list[str]) -> str:
    """Only the reel's two locked paper colors are allowed — anything else the
    model emits (drift, invention, a leftover from the old free-palette days)
    is discarded in favor of the locked rotation."""
    candidate = ""
    if isinstance(raw, dict):
        candidate = str(raw.get("bg") or raw.get("background") or "").strip()
    hex_color = ""
    if re.fullmatch(r"#?[0-9A-Fa-f]{6}", candidate or ""):
        hex_color = candidate if candidate.startswith("#") else f"#{candidate}"
        hex_color = hex_color.upper()
        if hex_color not in {c.upper() for c in bg_choices}:
            hex_color = ""
        if hex_color and hex_color == previous.upper():
            hex_color = ""
    if hex_color:
        return hex_color
    color = bg_choices[index % len(bg_choices)]
    if color.upper() == previous.upper():
        color = bg_choices[(index + 1) % len(bg_choices)]
    return color


def _norm_scene(
    raw: object, fallback_id: str, purpose: str, index: int, previous_bg: str, bg_choices: list[str]
) -> dict:
    data = raw if isinstance(raw, dict) else {}
    items = data.get("on_screen") or data.get("visuals") or []
    if not isinstance(items, list):
        items = []
    on_screen = []
    for i, item in enumerate(items):
        normalized = _norm_item(item, i)
        if normalized:
            on_screen.append(normalized)
    heading = [item for item in on_screen if item["type"] in {"text", "stat"}][:1]
    diagrams = [item for item in on_screen if item["type"] == "svg" and item["content"]][:1]
    extras = [item for item in on_screen if item["type"] == "arrow"][:1]
    if heading:
        heading[0]["emphasis"] = "high"
        heading[0]["reveal"] = 0.0
    else:
        title = _strong_copy(
            str(data.get("headline") or data.get("title") or data.get("narration") or purpose)
        )
        if title:
            heading = [
                {
                    "type": "text",
                    "content": title,
                    "emphasis": "high",
                    "reveal": 0.0,
                    "direction": "",
                }
            ]
    if diagrams:
        diagrams[0]["reveal"] = 0.28
    on_screen = heading + diagrams + extras
    narration = str(data.get("narration") or "").strip()
    return {
        "scene_id": str(data.get("scene_id") or fallback_id),
        "purpose": str(data.get("purpose") or purpose),
        "narration": narration,
        "bg": _norm_bg(data, index, previous_bg, bg_choices),
        "on_screen": on_screen,
    }


def normalize_reel(raw: dict, bg_choices: list[str]) -> dict:
    hook = _norm_scene(raw.get("hook"), "hook", "hook", 0, "", bg_choices)
    scenes_in = raw.get("scenes") or []
    if not isinstance(scenes_in, list):
        scenes_in = []
    scenes = []
    previous = hook["bg"]
    for i, item in enumerate(scenes_in[:6]):
        scene = _norm_scene(item, f"scene_{i + 1:02d}", "build", i + 1, previous, bg_choices)
        previous = scene["bg"]
        scenes.append(scene)
    payoff = _norm_scene(raw.get("payoff"), "payoff", "payoff", len(scenes) + 1, previous, bg_choices)
    beats = [hook, *scenes, payoff]
    if not any(beat["narration"] or beat["on_screen"] for beat in beats):
        return {}
    return {
        "title": str(raw.get("title") or "Explainer").strip()[:80],
        "hook": hook,
        "scenes": scenes,
        "payoff": payoff,
    }


def _join_models(labels: list[str]) -> str:
    seen: list[str] = []
    for label in labels:
        text = (label or "").strip()
        if text and text not in seen:
            seen.append(text)
    return " · ".join(seen)


def _with_models(reel: dict, *, svg_model: str = "", script_model: str = "") -> dict:
    if not reel:
        return reel
    if svg_model:
        reel["svg_model"] = svg_model
    if script_model:
        reel["script_model"] = script_model
    return reel


def _heading_of(beat: dict) -> str:
    for item in beat.get("on_screen") or []:
        if item.get("type") in {"text", "stat"}:
            return str(item.get("content") or "")
    return str(beat.get("scene_id") or "Idea")


def _fill_diagrams(
    reel: dict,
    palette: dict,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> dict:
    beats = [reel["hook"], *reel["scenes"], reel["payoff"]]
    missing = []
    for beat in beats:
        has_svg = False
        for item in beat["on_screen"]:
            if item.get("type") != "svg":
                continue
            fitted = extract_svg(item.get("content") or "", require_viewbox=True)
            if fitted:
                item["content"] = fitted
                has_svg = True
            else:
                item["content"] = ""
        beat["on_screen"] = [item for item in beat["on_screen"] if item.get("type") != "svg" or item.get("content")]
        if not has_svg:
            missing.append(beat)
    if not missing:
        return reel

    # One LLM call per beat that needs a diagram — run them together, since a
    # 6-beat reel would otherwise serialize six round-trips.
    def draw(beat: dict) -> tuple[str, str]:
        return illustrate_reel(
            _heading_of(beat),
            beat.get("narration") or "",
            openai_key,
            gemini_key,
            openai_model,
            gemini_model,
            palette=palette,
        )

    with ThreadPoolExecutor(max_workers=min(3, len(missing))) as pool:
        drawn = list(pool.map(draw, missing))

    svg_models: list[str] = []
    for beat, (svg, model) in zip(missing, drawn):
        if model:
            svg_models.append(model)
        if not svg:
            continue
        beat["on_screen"].insert(
            min(1, len(beat["on_screen"])),
            {
                "type": "svg",
                "content": svg,
                "emphasis": "medium",
                "reveal": 0.28,
                "direction": "",
            },
        )
    return _with_models(reel, svg_model=_join_models(svg_models))


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


def _openai_models(preferred: str) -> list[str]:
    # Two entries, not six: every extra fallback is another slow round-trip
    # before the user sees anything, and the deep chain was mostly retrying
    # models that fail for the same reason as the first.
    ordered = [preferred, "gpt-4.1"]
    seen = []
    for model in ordered:
        if model and model not in seen:
            seen.append(model)
    return seen


def _gemini_models(preferred: str) -> list[str]:
    ordered = [preferred, "gemini-2.5-pro", "gemini-2.5-flash"]
    seen = []
    for model in ordered:
        if model and model not in seen:
            seen.append(model)
    return seen


def _llm_json(
    system: str,
    user: str,
    bg_choices: list[str],
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
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
    )
    return normalize_reel(_parse_json(text), bg_choices) or {}


def _validated(raw: dict, answer: str) -> tuple[dict, list[str]]:
    model, err = parse_reel_model(raw)
    if not model:
        return {}, [err or "invalid reel json"]
    issues = reel_issues(model, answer)
    return model.dump(), issues


def _critique(
    draft: dict,
    question: str,
    answer: str,
    issues: list[str],
    bg_choices: list[str],
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> dict:
    user = (
        f"Original question:\n{question.strip()}\n\n"
        f"Source answer:\n{answer.strip()[:5000]}\n\n"
        f"Draft reel JSON:\n{json.dumps(draft, ensure_ascii=False)[:12000]}\n\n"
        f"Known issues:\n" + ("\n".join(f"- {row}" for row in issues) or "- none listed") + "\n\n"
        "Revise headings and narration against the rubric. Keep SVGs, bg, and beat count."
    )
    revised = _llm_json(
        CRITIQUE_SYSTEM,
        user,
        bg_choices,
        openai_key,
        gemini_key,
        openai_model,
        gemini_model,
    )
    return revised or draft


def _beat_bg(board: Storyboard, index: int, bg_choices: list[str], previous_bg: str) -> str:
    """Cut the paper often enough that the reel does not sit on one wash.

    Switch when role or emphasis changes, and at least every other beat.
    Cycle through the whole paper list instead of flipping two pastels.
    """
    papers = [c for c in bg_choices if c]
    if not papers:
        return previous_bg or "#FFF4C8"
    if index == 0:
        return papers[0]
    prev = board.beats[index - 1]
    beat = board.beats[index]
    should_cut = (
        beat.role != prev.role
        or beat.visual.emphasis != prev.visual.emphasis
        or index % 2 == 0
    )
    if not should_cut:
        return previous_bg or papers[0]
    try:
        here = next(i for i, c in enumerate(papers) if c.upper() == (previous_bg or "").upper())
    except StopIteration:
        here = -1
    return papers[(here + 1) % len(papers)]


def _shot_line(beat) -> str:
    visual = beat.visual
    return " ".join(
        part
        for part in (visual.sees, visual.changes, visual.motion)
        if part.strip()
    ).strip() or beat.heading or beat.narration


def _overlay_items(beat, *, photo: bool) -> list[dict]:
    heading = _strong_copy(beat.heading) or _strong_copy(beat.narration)
    if photo:
        lines = [o for o in beat.overlays if o.text.strip()]
        if not lines and heading:
            lines = [OverlayLine(text=heading, place="top", emphasis="high")]
        items: list[dict] = []
        for i, line in enumerate(lines[:4]):
            items.append(
                {
                    "type": "text",
                    "content": line.text.strip()[:90],
                    "emphasis": line.emphasis or ("high" if i == 0 else "medium"),
                    "reveal": min(0.08 * i, 0.4),
                    "direction": "",
                    "place": line.place or "top",
                }
            )
        return items
    on_screen: list[dict] = []
    if heading:
        on_screen.append(
            {
                "type": "text",
                "content": heading,
                "emphasis": "high",
                "reveal": 0.0,
                "direction": "",
                "place": "",
            }
        )
    return on_screen


def _render_storyboard(
    board: Storyboard,
    palette: dict,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    recorder: Recorder | None = None,
    mixed: bool = False,
    on_progress: Optional[ProgressFn] = None,
) -> dict:
    """Storyboard -> the existing reel JSON shape.

    Beats are assembled directly rather than run through normalize_reel: the
    storyboard already decided the composition, including that some beats get
    no diagram at all, which the legacy normalizer would overwrite.
    """
    world_brief = board.world.brief()
    drawable = [
        i
        for i, beat in enumerate(board.beats)
        if beat.visual.medium == "svg" and beat.visual.wants_diagram()
    ]
    rendered: dict[int, str] = {}

    last = len(board.beats) - 1

    def shot_of(index: int) -> dict:
        beat = board.beats[index]
        shot = beat.visual.model_dump()
        shot["heading"] = beat.heading
        shot["narration"] = beat.narration
        return shot

    def budget_note(index: int) -> str:
        """Complexity has to fall at the end, or the payoff reads as another
        busy diagram instead of the moment it all resolves."""
        beat = board.beats[index]
        if index == last or beat.role == "payoff":
            return (
                "This is the PAYOFF — the last frame of the film. It must be the SIMPLEST "
                "frame in the reel: at most 4 elements, nothing that is not part of the "
                "realization. Strip labels, strip scaffolding, keep the one thing that makes "
                "it click. If the middle beats were busy, this one is a clean, quiet image."
            )
        if beat.visual.emphasis == "quiet":
            return (
                "This is a QUIET beat: fewer elements, more air — still a drawing of the world, "
                "not an empty slide."
            )
        if beat.visual.emphasis == "loud":
            return (
                "This is a LOUD beat: fill the tall frame. Use 8 to 12 "
                "<g class=\"r-el\"> groups — boxes, at least 2 arrows, and labels that pop "
                "after each arrow. Empty sky is a failed shot."
            )
        return (
            "This is a NORMAL beat: 8 to 12 <g class=\"r-el\"> groups. Build the diagram "
            "in reveal order: body, arrow, label, body, arrow, label. At least 2 arrows "
            "and 4 short labels. The change from the previous shot must be obvious."
        )

    def draw(index: int, reference: str) -> tuple[str, str]:
        try:
            return render_scene(
                shot_of(index),
                world_brief,
                openai_key,
                gemini_key,
                openai_model,
                gemini_model,
                reference_svg=reference,
                extra_note=budget_note(index),
                palette=palette,
                recorder=recorder,
            )
        except Exception as exc:
            logger.warning("scene render failed for beat %s: %s", index, exc)
            return "", ""

    # The opening shot establishes the world; every later shot is drawn in
    # parallel against it, so recurring objects keep their geometry without
    # each frame waiting on the one before.
    svg_models: list[str] = []
    media_jobs = sum(1 for b in board.beats if mixed and b.visual.medium in {"sora", "image"})
    total_work = max(1, len(drawable) + media_jobs)
    done_work = 0

    def tick(what: str) -> None:
        nonlocal done_work
        done_work += 1
        _report(
            on_progress,
            "drawing",
            f"{what} · {done_work} of {total_work} shots done",
            0.2 + 0.75 * done_work / total_work,
        )

    if drawable:
        _report(on_progress, "drawing", f"Drawing shot 1 of {total_work}", 0.2)
        first = drawable[0]
        svg, model = draw(first, "")
        rendered[first] = svg
        if model:
            svg_models.append(model)
        tick("Drawing")
        reference = svg
        rest = drawable[1:]
        if rest:
            with ThreadPoolExecutor(max_workers=min(DRAW_WORKERS, len(rest))) as pool:
                futures = {pool.submit(draw, index, reference): index for index in rest}
                for fut in as_completed(futures):
                    svg, model = fut.result()
                    rendered[futures[fut]] = svg
                    if model:
                        svg_models.append(model)
                    tick("Drawing")

    media: dict[int, dict] = {}
    if mixed:
        jobs: list[tuple[int, str]] = [
            (i, beat.visual.medium)
            for i, beat in enumerate(board.beats)
            if beat.visual.medium in {"sora", "image"}
        ]

        def _gen_media(pair: tuple[int, str]):
            index, kind = pair
            beat = board.beats[index]
            shot = _shot_line(beat)
            words = len((beat.narration or "").split())
            try:
                if kind == "sora":
                    from sora import generate_beat_video

                    return index, kind, generate_beat_video(
                        title=board.title,
                        world_brief=world_brief,
                        shot=shot,
                        word_count=words,
                        recorder=recorder,
                    )
                from image_gen import generate_still

                return index, kind, generate_still(
                    title=board.title,
                    world_brief=world_brief,
                    shot=shot,
                    openai_key=openai_key,
                    recorder=recorder,
                )
            except Exception as exc:
                logger.warning("%s render failed for beat %s: %s", kind, index, exc)
                return index, kind, None

        if jobs:
            with ThreadPoolExecutor(max_workers=min(3, len(jobs))) as pool:
                for index, kind, payload in pool.map(_gen_media, jobs):
                    if payload:
                        media[index] = {"kind": kind, **payload}
                    tick("Filming")

        # A blocked Sora beat should become a drawn beat, not an empty photo frame.
        for index, beat in enumerate(board.beats):
            got = media.get(index)
            if beat.visual.medium in {"sora", "image"} and not got:
                beat.visual.medium = "svg"
                if beat.visual.wants_diagram() and index not in rendered:
                    svg, model = draw(index, next(iter(rendered.values()), ""))
                    rendered[index] = svg
                    if model:
                        svg_models.append(model)

    beats: list[dict] = []
    previous_bg = ""
    for index, beat in enumerate(board.beats):
        photo = beat.visual.medium in {"sora", "image"}
        on_screen = _overlay_items(beat, photo=photo)
        if not photo:
            svg = extract_svg(rendered.get(index) or "", require_viewbox=True)
            if svg:
                on_screen.append(
                    {
                        "type": "svg",
                        "content": svg,
                        "emphasis": "medium",
                        "reveal": 0.28,
                        "direction": "",
                        "place": "",
                    }
                )
        bg = _beat_bg(board, index, palette["bg"], previous_bg)
        previous_bg = bg
        row = {
            "scene_id": "hook" if index == 0 else ("payoff" if index == len(board.beats) - 1 else f"beat_{index:02d}"),
            "purpose": beat.role,
            "narration": beat.narration.strip(),
            "bg": bg,
            "on_screen": on_screen,
            "visual": beat.visual.model_dump(),
            "media": beat.visual.medium,
        }
        payload = media.get(index)
        if payload and payload.get("kind") == "sora":
            row["video"] = {k: v for k, v in payload.items() if k != "kind"}
        elif payload and payload.get("kind") == "image":
            row["image"] = {k: v for k, v in payload.items() if k != "kind"}
        beats.append(row)

    if len(beats) < 3 or not any(b["narration"] or b["on_screen"] for b in beats):
        return {}
    return _with_models(
        {
            "title": (board.title or "Explainer").strip()[:80],
            "hook": beats[0],
            "scenes": beats[1:-1],
            "payoff": beats[-1],
            "world": board.world.model_dump(),
        },
        svg_model=_join_models(svg_models),
    )


def plan_reel(
    question: str,
    answer: str,
    title: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    prior_world: str = "",
    director_system: str = "",
    script_model: str = "",
    position: str = "middle",
    recorder: Recorder | None = None,
    visual_engine: str = "svg",
    on_progress: Optional[ProgressFn] = None,
) -> dict:
    palette = pick_palette()
    recorder = recorder or Recorder()
    reel_clock = timed()
    reel_clock.__enter__()

    mixed = visual_engine == "sora"
    _report(on_progress, "storyboard", "Planning the shots", 0.03)
    board = plan_storyboard(
        question,
        title,
        answer,
        openai_key,
        gemini_key,
        openai_model,
        gemini_model,
        prior_world,
        director_system=director_system,
        position=position,
        recorder=recorder,
        mixed=mixed,
    )
    if board:
        issues = storyboard_issues(board)
        if issues:
            logger.info("storyboard issues: %s", "; ".join(issues[:6]))
            board, changes = critique_storyboard(
                board, issues, openai_key, gemini_key, openai_model, gemini_model, recorder
            )
            if changes:
                logger.info("storyboard critic: %s", "; ".join(changes[:6]))
        if mixed:
            assign_mixed_media(board)
        _report(on_progress, "storyboard", f"Storyboard ready · {len(board.beats)} shots", 0.18)
        # Narration is final once the storyboard is approved, but SVG rendering
        # still has ~a minute to run. Start synthesizing speech now so the clips
        # are on disk before the reel ever reaches the player.
        audio_cache.warm_reel_in_background(
            {
                "hook": {"narration": board.beats[0].narration} if board.beats else None,
                "scenes": [{"narration": b.narration} for b in board.beats[1:]],
                "payoff": None,
            },
            os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-athena-en"),
            os.getenv("DEEPGRAM_API_KEY", "").strip(),
        )
        reel = _render_storyboard(
            board,
            palette,
            openai_key,
            gemini_key,
            openai_model,
            gemini_model,
            recorder,
            mixed=mixed,
            on_progress=on_progress,
        )
        if reel and mixed:
            reel["visual_engine"] = "sora"
            labels = [reel.get("svg_model") or ""]
            for beat in (reel.get("hook"), *(reel.get("scenes") or []), reel.get("payoff")):
                if not beat:
                    continue
                if beat.get("video"):
                    labels.append(f"Sora {beat['video'].get('model') or 'sora-2'}")
                if beat.get("image"):
                    labels.append(beat["image"].get("model") or "gpt-image-1")
            reel["svg_model"] = _join_models(labels)
        if reel:
            model, err = parse_reel_model(reel)
            if model:
                dumped = model.dump()
                out = _with_models(
                    dumped,
                    svg_model=reel.get("svg_model") or dumped.get("svg_model") or "",
                    script_model=script_model,
                )
                return _with_metrics(out, recorder, reel_clock)
            logger.warning("storyboard reel failed schema, falling back: %s", str(err)[:200])

    logger.info("falling back to the legacy single-call director")
    _report(on_progress, "drawing", "Retrying with a simpler director", 0.5)
    legacy = _with_models(
        _plan_reel_legacy(
            question, answer, title, palette, openai_key, gemini_key, openai_model, gemini_model
        ),
        script_model=script_model,
    )
    return _with_metrics(legacy, recorder, reel_clock)


def _with_metrics(reel: dict, recorder: Recorder, clock: timed) -> dict:
    """Attach the production record. `directed_seconds` is the director's own
    wall time, which is less than the sum of steps because scene renders run
    in parallel — both numbers are worth seeing."""
    if not reel:
        return reel
    clock.__exit__()
    steps = recorder.dump()
    reel["metrics"] = {
        "steps": steps,
        "directed_seconds": round(clock.seconds, 3),
        "llm_seconds": round(sum(s["seconds"] for s in steps), 3),
        "input_tokens": sum(s["input_tokens"] for s in steps),
        "output_tokens": sum(s["output_tokens"] for s in steps),
        "calls": sum(s["calls"] for s in steps),
        "cost_usd": (
            round(sum(s["cost_usd"] for s in steps if s["cost_usd"] is not None), 6)
            if any(s["cost_usd"] is not None for s in steps)
            else None
        ),
        "unpriced_models": sorted(
            {s["model"] for s in steps if s["cost_usd"] is None and s["model"]}
        ),
    }
    return reel


def _plan_reel_legacy(
    question: str,
    answer: str,
    title: str,
    palette: dict,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> dict:
    bg_choices = palette["bg"]
    director_system = _render_director_system(palette)
    user = (
        f"Original question:\n{question.strip()}\n\n"
        f"Answer title: {title.strip() or 'Notes'}\n\n"
        f"Complete answer from the Answer Agent:\n{answer.strip()[:5000]}\n\n"
        "Direct a vertical phone reel. This is one lesson in a series when prior context is given — continue that film. Headings must reuse a noun or number from the answer. "
        "Each beat: one short heading, then an SVG inside the 20px margin. Narration 1–3 sentences."
    )
    draft = _llm_json(
        director_system, user, bg_choices, openai_key, gemini_key, openai_model, gemini_model
    )
    if not draft:
        return {}
    planned, issues = _validated(draft, answer)
    if not planned:
        retry_user = user + "\n\nYour last JSON failed schema:\n" + "\n".join(issues[:8])
        draft = _llm_json(
            director_system, retry_user, bg_choices, openai_key, gemini_key, openai_model, gemini_model
        )
        planned, issues = _validated(draft, answer)
        if not planned:
            return {}
    # The critique pass is a second full-size LLM call. Only pay for it when
    # validation actually found something wrong.
    if issues:
        critiqued = _critique(
            planned, question, answer, issues, bg_choices, openai_key, gemini_key, openai_model, gemini_model
        )
        final, _ = _validated(critiqued, answer)
        chosen = final or planned
    else:
        chosen = planned
    return _fill_diagrams(chosen, palette, openai_key, gemini_key, openai_model, gemini_model)
