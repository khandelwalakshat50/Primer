from __future__ import annotations

import re

from openai import OpenAI
from google import genai
from google.genai import types

from metrics import Recorder, timed, usage_from_gemini, usage_from_openai
from svg_fit import (
    OVERFLOW_NOTE,
    normalize_reel_svg,
    overlap_report,
    parse_viewbox,
    sanitize_svg_markup,
)

SVG_SYSTEM = """You are a notebook illustrator. For each lesson fragment you draw one small diagram in SVG.

Output rules
- Return only one <svg>...</svg> element. No markdown fences, no commentary.
- REQUIRED: viewBox="0 0 400 160" and width="100%" height="100%". Never omit viewBox.
- Safe content box: keep every shape and label inside x:20-380, y:16-144 (20px inset from the viewBox).
- White or transparent background. Ink #171717. Fills #F4B942, #2A9D8F, #5B8DEF. Accent #E23B2A.
- Simple geometry: lines, arrows, circles, rectangles, labeled parts. Use more than one fill so the drawing is not monochrome.
- Text labels: sans-serif, 11-13px, fill #171717. Keep labels short.
- No <script>, no foreignObject, no iframes, no external URLs, no event handlers, no animation that runs script.
- No position:absolute. Do not size the drawing with width/height in px instead of viewBox.
- Illustrate the core idea of the handwriting. Do not copy long sentences into the drawing.

GOOD: <svg viewBox="0 0 400 160"><rect x="40" y="40" width="120" height="80"/><text x="52" y="84">in</text></svg>
BAD: <svg width="400px" height="160px"><rect x="-10" y="0" width="500" height="200"/></svg> (no viewBox, shapes leave the frame)
"""

REEL_SVG_SYSTEM = """You draw one clear educational diagram for a phone-screen beat, built to animate on-screen — not just appear.

Output rules
- Return only one <svg>...</svg>. No markdown, no commentary.
- REQUIRED: viewBox="0 0 360 440". Never omit it. Do not use width/height in px as a substitute.
- Safe content box: ALL shapes, arrows, and labels must stay inside x:20-340, y:20-420 (20px margin inside the viewBox). Center the drawing in that box.
- Transparent background. Ink __INK__. Object fills __FILL1__, __FILL2__, __FILL3__ — use all three across the drawing so objects read as colored, not pale paper. Accent __ACCENT__ for the one detail that changed.
- This diagram is one beat in a reel that has already locked its palette — use exactly these colors, do not introduce any other hex value. Do not fill every shape with the same wash.

STRUCTURE — this is what makes the reveal animation work
- Aim for 8 to 12 <g class="r-el"> groups on a normal beat. Each <g> is exactly ONE idea: one arrow, one box, one icon, or one label. Never put two unrelated shapes in the same group, and never merge everything into one giant group or one complex multi-segment path.
- Fill the tall frame: boxes, arrows that POINT at things, and labels that POP in after the arrow. A sparse 2-box diagram with empty sky is a failed drawing.
- Every <g> must carry class="r-el", e.g. <g class="r-el">...</g>. The frontend reveals groups one at a time in this exact DOM order, so order them: body → arrow → label → next body → arrow → label.
- Lines, arrows, and circles are STROKE shapes, not filled shapes: fill="none" stroke="__INK__" stroke-width="3". Draw an arrow as a <line> (or <polyline>) shaft plus a short two-segment <polyline> chevron for the head — never a filled <polygon> arrowhead, never a single path fusing shaft and head together.
- Boxes and icons are FILLED shapes (rect/circle with a fill color) — each still its own <g class="r-el">, never merged with a neighboring box into one path.
- 5–10 short labels (12–16px), each in its own <g class="r-el"> so the player can pop the text after the arrow that points at it. 1–4 words. No paragraphs.
- At least 2 arrows (or 1 on a quiet beat) connecting or pointing at objects. The beat should feel like a diagram coming together, not a still life.
- NEVER overlap labels or boxes — overlapping type looks unprofessional and is a failed drawing. Scatter across the tall frame (upper / middle / lower thirds, or left and right columns). Labels live in empty sky beside or below objects, never on a fill. Boxes connect with arrows across a gap; they must not share pixels.
- No tiny hairlines. No <script>, foreignObject, iframe, external URLs, event handlers, or position:absolute.

GOOD:
<svg viewBox="0 0 360 440">
  <g class="r-el">
    <rect x="40" y="70" width="120" height="70" rx="8" fill="__FILL1__"/>
  </g>
  <g class="r-el">
    <line x1="170" y1="105" x2="210" y2="105" stroke="__INK__" stroke-width="3" fill="none"/>
    <polyline points="202,97 218,105 202,113" stroke="__INK__" stroke-width="3" fill="none"/>
  </g>
  <g class="r-el">
    <text x="100" y="112" font-size="14" text-anchor="middle" fill="__INK__">old bond</text>
  </g>
  <g class="r-el">
    <rect x="220" y="70" width="120" height="70" rx="8" fill="__FILL2__"/>
  </g>
  <g class="r-el">
    <text x="280" y="112" font-size="14" text-anchor="middle" fill="__INK__">new bond</text>
  </g>
  <g class="r-el">
    <rect x="40" y="200" width="280" height="64" rx="8" fill="__FILL3__"/>
  </g>
  <g class="r-el">
    <line x1="180" y1="148" x2="180" y2="192" stroke="__INK__" stroke-width="3" fill="none"/>
    <polyline points="172,184 180,200 188,184" stroke="__INK__" stroke-width="3" fill="none"/>
  </g>
  <g class="r-el">
    <text x="180" y="238" font-size="14" text-anchor="middle" fill="__INK__">yield up</text>
  </g>
</svg>
(bodies, arrows, then labels — the frame is full from top to bottom)

BAD:
<svg width="360" height="300">
  <path d="M40,90 L150,90 L150,170 L40,170 Z" fill="__INK__"/>
</svg>
(no viewBox; one fused path; empty frame; nothing to reveal)
"""

SCENE_RENDER_SYSTEM = """You are the artist on an explainer film. The director has already decided the shot. Your only job is to draw THAT shot, faithfully, as SVG.

You are not deciding a new concept. You MAY add arrows that show the relationship and short labels on every important object — those are how the shot reads on a phone. Do not add a second topic.

DRAW THE SHOT
1. Read `sees` — that is the frame. Draw that world, then ANNOTATE it: arrows between related pieces, a label on each box.
2. Read `keeps` — those elements already exist in this reel's world. When a reference SVG is given, REUSE its geometry for them: same position, same size, same shape. The viewer must recognize it as the same object, not a redrawn cousin.
3. Read `changes` and `focus` — the changing thing is the subject of the frame. Give it the accent color and the most visual weight.

THE CHANGE MUST BE OBVIOUS
If a reference SVG is given, the viewer should know this is a NEW shot in one second. Reuse geometry for `keeps`, but the CHANGE has to be large: a new piece, a new height, a split, a travel, a color shift to accent. Redrawing the reference with a tiny tweak or a different label is a failed shot.

DENSITY — THIS IS THE MAIN FAILURE MODE
Empty sky is a failed shot. Normal and loud beats should feel BUSY in a good way: typically 8 to 12 <g class="r-el"> groups.
Required on a normal beat:
- at least 3 filled bodies (boxes/circles)
- at least 2 arrows (shaft + chevron, each their own group or shaft+head together in one arrow group)
- at least 4 text labels in their OWN groups so they pop AFTER the arrow that points at them
Reveal order: body, arrow, label, body, arrow, label… so the beat plays like a diagram being built, not a postcard.
Quiet and payoff beats can be simpler (4–6 groups) but still fill the tall frame — never a tiny icon in the middle of the phone.

COMPOSITION FOR A PHONE
The canvas is TALL (360 wide x 440 high). USE THE HEIGHT AND THE WIDTH.
Scatter objects: one cluster in the upper third, one in the middle, one in the lower third — or a left column and a right column. Do not pile every box on the same (180, 220) point.
Bodies should be LARGE. A box that is only 60×40 in a 360×440 frame looks like a postage stamp. Prefer boxes 100×70 or bigger.

NO OVERLAP — overlapping text or boxes looks unprofessional and is a failed drawing
- No two labels may share space. Clear gap of at least 16 units between label boxes.
- No label may sit on top of a box, circle, arrow, or another label. Put the label beside or clearly below the thing it names, in empty sky.
- No two filled bodies may occupy the same pixels. If two ideas are related, connect them with an arrow across the gap — do not stack them on top of each other.
- Arrows may TOUCH the edge of a box they point at, but they must not run through a label.
- Before you finish, imagine a bounding box around every group. If any two boxes (except an arrowhead kissing a box) intersect, move one. Spread them farther apart; you have a tall empty phone to use.

TECHNICAL REQUIREMENTS (satisfy these while drawing the shot above — they do not change what you draw)
- Return only one <svg>...</svg>. No markdown, no commentary.
- REQUIRED viewBox="0 0 360 440". Never width/height in px instead.
- Keep everything inside x:20-340, y:20-420.
- Wrap each separately-revealed element in <g class="r-el"> — one group per element that should appear on its own. Prefer 8–12 groups on a normal beat (box, arrow, label, box, arrow, label…).
- Lines, arrows and rings are STROKE shapes: fill="none" stroke="__INK__" stroke-width="3". Arrowheads are a short <polyline> chevron, never a filled polygon. Solid bodies are FILLED shapes.
- Palette only: ink __INK__; object fills __FILL1__, __FILL2__, __FILL3__ (different objects get different fills — they should look painted, not like pale paper); accent __ACCENT__ for the changing thing. No other colors.
- Put font-size and text-anchor on the <text> element itself.
- No <script>, foreignObject, iframe, external URLs, event handlers, or position:absolute.

GOOD (shot: "one accent bill slides on top of the stack; $110")
<svg viewBox="0 0 360 440">
  <g class="r-el"><rect x="110" y="280" width="160" height="40" rx="3" fill="__FILL1__" stroke="__INK__" stroke-width="3"/></g>
  <g class="r-el"><text x="40" y="306" font-size="14" fill="__INK__">year 1</text></g>
  <g class="r-el"><rect x="110" y="220" width="160" height="40" rx="3" fill="__FILL2__" stroke="__INK__" stroke-width="3"/></g>
  <g class="r-el"><text x="40" y="246" font-size="14" fill="__INK__">year 2</text></g>
  <g class="r-el"><rect x="110" y="160" width="160" height="40" rx="3" fill="__ACCENT__" stroke="__INK__" stroke-width="3"/></g>
  <g class="r-el"><text x="40" y="186" font-size="14" fill="__INK__">new bill</text></g>
  <g class="r-el"><line x1="280" y1="180" x2="310" y2="108" stroke="__INK__" stroke-width="3" fill="none"/></g>
  <g class="r-el"><polyline points="302,118 310,102 288,112" stroke="__INK__" stroke-width="3" fill="none"/></g>
  <g class="r-el"><text x="180" y="72" font-size="48" text-anchor="middle" fill="__INK__">$110</text></g>
</svg>
(labels sit in the left gutter, boxes are stacked with a gap, the number is in empty sky — nothing shares pixels)

BAD
<svg viewBox="0 0 360 440">
  <g class="r-el"><rect x="80" y="160" width="200" height="120" fill="__FILL1__"/></g>
  <g class="r-el"><rect x="90" y="170" width="200" height="120" fill="__FILL2__"/></g>
  <g class="r-el"><text x="180" y="220" font-size="16" text-anchor="middle">old</text></g>
  <g class="r-el"><text x="185" y="224" font-size="16" text-anchor="middle">new</text></g>
</svg>
(boxes stacked on the same spot; two labels occupying one word)
"""

_DEFAULT_PALETTE = {
    "ink": "#1C1917",
    "bg": ["#FFF4C8", "#FFD9B8", "#C8F0E0", "#FFD0D0"],
    "fills": ["#F4B942", "#2A9D8F", "#F07A4A"],
    "accent": "#E23B2A",
}


def _apply_palette(template: str, palette: dict | None) -> str:
    p = palette or _DEFAULT_PALETTE
    papers = p.get("bg") or _DEFAULT_PALETTE["bg"]
    fills = p.get("fills") or papers
    fill1 = fills[0] if fills else _DEFAULT_PALETTE["fills"][0]
    fill2 = fills[1] if len(fills) > 1 else fill1
    fill3 = fills[2] if len(fills) > 2 else fill2
    return (
        template.replace("__INK__", p.get("ink") or _DEFAULT_PALETTE["ink"])
        .replace("__FILL1__", fill1)
        .replace("__FILL2__", fill2)
        .replace("__FILL3__", fill3)
        .replace("__ACCENT__", p.get("accent") or _DEFAULT_PALETTE["accent"])
    )


def _render_reel_svg_system(palette: dict | None) -> str:
    return _apply_palette(REEL_SVG_SYSTEM, palette)

_FORBIDDEN = re.compile(
    r"<(script|foreignObject|iframe|object|embed|link|meta)[\s\S]*?</\1>|<(script|foreignObject|iframe|object|embed)[^>]*/>",
    re.I,
)


def extract_svg(raw: str, *, require_viewbox: bool = False) -> str:
    text = sanitize_svg_markup((raw or "").strip())
    text = re.sub(r"^```(?:svg)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"<svg\b[\s\S]*?</svg>", text, re.I)
    if not match:
        return ""
    svg = match.group(0)
    svg = _FORBIDDEN.sub("", svg)
    svg = re.sub(r"\son[a-z]+\s*=\s*('[^']*'|\"[^\"]*\")", "", svg, flags=re.I)
    svg = re.sub(r"javascript:", "", svg, flags=re.I)
    svg = re.sub(r"(href|xlink:href|src)\s*=\s*['\"]\s*(?!#)", 'data-dropped="', svg, flags=re.I)
    if not re.search(r"<svg\b", svg, re.I):
        return ""
    if parse_viewbox(svg) is None:
        if require_viewbox:
            return ""
        svg = re.sub(r"<svg\b", '<svg viewBox="0 0 400 160"', svg, count=1, flags=re.I)
    if require_viewbox:
        return normalize_reel_svg(svg)
    return svg.strip()


def _prompt(title: str, handwriting: str) -> str:
    return (
        f"Notebook heading: {title or 'Notes'}\n\n"
        f"Handwriting to illustrate:\n{handwriting.strip()[:1200]}"
    )


def _complete_svg(
    system: str,
    prompt: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    *,
    reel: bool,
    recorder: Recorder | None = None,
    step: str = "svg_render",
    step_label: str = "SVG creation",
) -> tuple[str, str]:
    require = reel

    def take(text: str) -> str:
        return extract_svg(text, require_viewbox=require)

    import llm

    text, model = llm.complete(
        system,
        prompt,
        openai_key_value=openai_key,
        gemini_key_value=gemini_key,
        openai_model_name=openai_model,
        gemini_model_name=gemini_model,
        recorder=recorder,
        step=step,
        step_label=step_label,
        max_tokens=8192,
    )
    svg = take(text)
    if svg:
        return svg, model
    return "", ""


def illustrate(title: str, handwriting: str, openai_key: str, gemini_key: str, openai_model: str, gemini_model: str) -> str:
    svg, _model = _complete_svg(
        SVG_SYSTEM,
        _prompt(title, handwriting),
        openai_key,
        gemini_key,
        openai_model,
        gemini_model,
        reel=False,
    )
    return svg


def render_scene(
    shot: dict,
    world_brief: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    reference_svg: str = "",
    extra_note: str = "",
    palette: dict | None = None,
    recorder: Recorder | None = None,
) -> tuple[str, str]:
    """Draw one storyboarded shot.

    `reference_svg` is the already-rendered SVG of the beat that established
    this reel's world. Passing it is what makes a recurring object keep its
    geometry instead of being reinvented every beat.
    """
    lines = [
        f"THIS REEL'S VISUAL WORLD:\n{world_brief or '(none declared)'}",
        "",
        "THE SHOT TO DRAW:",
        f"- sees: {shot.get('sees') or shot.get('heading') or 'the idea'}",
        f"- keeps from previous beat: {shot.get('keeps') or '(nothing — this establishes the world)'}",
        f"- changes: {shot.get('changes') or '(first beat)'}",
        f"- viewer focus: {shot.get('focus') or 'the subject'}",
        f"- motion: {shot.get('motion') or '(none specified)'}",
        f"- visual type: {shot.get('type') or 'object'}",
    ]
    if shot.get("heading"):
        lines.append(f"- on-screen heading (already shown above the drawing, do not repeat it): {shot['heading']}")
    if shot.get("narration"):
        lines.append(f"- spoken over this shot: {str(shot['narration'])[:400]}")
    if reference_svg:
        lines += [
            "",
            "REFERENCE — the SVG of the opening shot, which established this reel's world. "
            "Reuse the geometry of anything listed under `keeps` (same coordinates, size and "
            "shape). Then make `changes` unmissable — if this frame could be mistaken for the "
            "reference, draw again:",
            reference_svg[:2600],
        ]
    if extra_note:
        lines += ["", extra_note]
    lines += ["", "Draw the shot."]
    prompt = "\n".join(lines)
    system = _apply_palette(SCENE_RENDER_SYSTEM, palette)

    svg, model = _complete_svg(
        system, prompt, openai_key, gemini_key, openai_model, gemini_model,
        reel=True, recorder=recorder,
    )
    if svg:
        # Colliding words are always a defect, and no amount of fitting can
        # separate them — so redraw once with the specific collision named.
        collisions = overlap_report(svg)
        if collisions:
            note = (
                "Your last attempt had overlapping drawing: "
                + "; ".join(collisions[:4])
                + ". Redraw the SAME shot but SCATTER it across the tall 360x440 canvas. "
                "Every label in empty sky beside or below its object (never on the fill). "
                "No two boxes sharing pixels — put a clear gap between them and connect with an arrow. "
                "Use the top, middle, AND bottom of the phone, and the left and right. "
                "Keep the same subject and meaning."
            )
            retry, retry_model = _complete_svg(
                system,
                f"{prompt}\n\n{note}",
                openai_key,
                gemini_key,
                openai_model,
                gemini_model,
                reel=True,
                recorder=recorder,
                step="svg_overlap_redraw",
                step_label="SVG redraw (overlap)",
            )
            if retry and not overlap_report(retry):
                return retry, retry_model
            if retry and len(overlap_report(retry)) < len(collisions):
                return retry, retry_model
        return svg, model
    # A frame that overflowed or came back without a viewBox is dropped by
    # normalization, which would silently leave this beat undrawn. Retry once.
    return _complete_svg(
        system,
        f"{prompt}\n\n{OVERFLOW_NOTE}",
        openai_key,
        gemini_key,
        openai_model,
        gemini_model,
        reel=True,
        recorder=recorder,
        step="svg_retry",
        step_label="SVG retry (overflowed)",
    )


def illustrate_reel(
    heading: str,
    narration: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    extra_note: str = "",
    palette: dict | None = None,
) -> tuple[str, str]:
    prompt = (
        f"Heading on screen: {heading or 'Idea'}\n\n"
        f"Spoken narration for this beat:\n{narration.strip()[:800]}\n\n"
        "Draw the idea. One diagram. Short labels only. Stay inside the 20px margin."
    )
    if extra_note:
        prompt += f"\n\n{extra_note}"
    svg, model = _complete_svg(
        _render_reel_svg_system(palette),
        prompt,
        openai_key,
        gemini_key,
        openai_model,
        gemini_model,
        reel=True,
    )
    if svg:
        return svg, model
    if extra_note:
        return "", ""
    return illustrate_reel(
        heading,
        narration,
        openai_key,
        gemini_key,
        openai_model,
        gemini_model,
        extra_note=OVERFLOW_NOTE,
        palette=palette,
    )
