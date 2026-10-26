from __future__ import annotations

import json
import re

from openai import OpenAI
from google import genai
from google.genai import types

from curriculum import Curriculum, ReelSpec
from metrics import Recorder, timed, usage_from_gemini, usage_from_openai

COHERENCY_SYSTEM = """You are the continuity and pedagogy editor for a course of short EXPLAINER VIDEOS. You run after a lesson's script is drafted and before it goes to the visual director. You can see the whole course plan and the full scripts of every lesson already completed.

Your standard: a curious person who knows nothing about this topic, watching on a phone, learning SLOWLY, one idea at a time. They cannot pause to look anything up. Judge the draft only by whether it lands ONE idea for that person.

Check the draft for these, in order:

0. ONE IDEA, NOT THREE (most important)
Does this script try to teach more than the one idea in its objective? Explainer videos fail by cramming. If the draft mentions extra facts, side notes, caveats, or "also worth knowing" material, CUT that material — do not compress it, delete it. A script that teaches its one idea and stops is correct, even if it feels short. Subject → problem → example → turn → land all counting as ONE idea is correct. Do not cut the story down to a definition.

0a. CAN YOU TELL WHAT WE ARE DISCUSSING?
The first sentence must name the subject in plain words (the course topic, and on later lessons the spine plus this step). If a stranger could not tell what the video is about from line one, rewrite the opening. This is not a syllabus preview and not "in this video we will cover". Keep it.

0b. IS IT A STORY, OR A PILE OF SENTENCES?
A good draft has a familiar setup, a problem that doesn't fit, a walked-through instance, a because-of-that turn, and a landing that returns to the opening situation. If the opening is interesting and the second half is a generic explanation with no object in motion, rewrite the second half as the continuation of the same story. Do not replace story with a recap.

0c. IS ANYTHING ACTUALLY SHOWN?
Does the script walk through a concrete example with real numbers or a real object, or does it only define and assert? If it is abstract, rewrite it to lead with the specific example named in the plan after the subject line.

0d. WILL ANYONE REMEMBER THIS?
Does the script sound like a generic summary ("X is a process by which", "there are several types")? If so, rewrite it around one object or number, one cause, and a keepable last line. Cut only empty hype ("have you ever wondered", "let's dive in"), not the subject line and not genuine tension.

0e. WORDS THE VIEWER DOES NOT OWN YET
Flag any term used before anything has shown what it means, and that no dependency lesson established. Either cut the term or let the sentence before it show the thing. Assume no jargon tolerance at all.

1. TERMINOLOGY CONSISTENCY
Does this script use a term differently than an earlier lesson defined it? If so, rewrite those lines to match the earlier definition. A term means one thing for the whole course.

2. NO REDUNDANT RE-TEACHING
Does this script re-explain something an earlier lesson already covered in depth? Compress or cut the re-explanation. Replace it with a one-line callback instead, naming the earlier lesson ("remember the order book from the first video") rather than teaching the concept again. Naming the subject and the spine in the first sentence is NOT re-teaching — leave it.

3. PROGRESSION
Does this script assume something the learner hasn't been given yet — a term or idea that belongs to a lesson not listed in depends_on? Flag it and either cut the assumption or add the one line needed to earn it. Also check the opposite: is this script too elementary for its place in the curriculum, re-explaining something so basic it insults a learner who just finished the dependencies?

4. CONTINUITY (required on every lesson after the first)
This course is one YouTube film cut into episodes. The draft MUST open as the next episode: after naming the subject, it picks up the previous lesson's last state — same object, next cause. If the draft could play as a standalone video (new analogy, cold definition, skipped chapter), rewrite the opening. Do not recap the whole course; one handoff is enough.

5. SAME STORY, SAME WAVELENGTH (fatal on middle and last lessons)
The course has a spine — one object or situation. This draft must be the next chapter of that story. If it starts a new analogy, drops the object halfway, or turns into a recap/moral in the second half, rewrite it so the same thing keeps moving. Smallest edit that puts the story back on the rails. Do not "fix" this by adding a new topic.

Make the smallest edit that fixes a real problem. Do not rewrite lines that are already fine.
Cutting is always allowed and usually right: a shorter script that lands one idea beats a complete one that lands none. Never add new teaching material to "round it out".

OUTPUT
JSON only, no markdown, no commentary:
{
  "approved": true or false,
  "script": "the script to use: identical to the draft if approved as-is, otherwise your revised version",
  "changes": ["short note on what changed and why", ...]
}
If the draft passes all four checks, set approved true, return it unchanged as script, and changes as an empty list.
"""


def _prior_block(prior: list[ReelSpec]) -> str:
    if not prior:
        return "(none - this is the first lesson in the course)"
    parts = []
    for p in prior:
        script = p.narration_draft or "(script not available)"
        parts.append(
            f"### {p.reel_id} - {p.title}\n"
            f"Established terms: {', '.join(p.key_terms)}\n"
            f"Script:\n{script}"
        )
    return "\n\n".join(parts)


def _curriculum_map(curriculum: Curriculum) -> str:
    lines = []
    for r in curriculum.reels:
        lines.append(
            f"- {r.reel_id} ({r.status}): {r.title} - {r.objective} "
            f"[depends_on: {', '.join(r.depends_on) or 'none'}]"
        )
    return "\n".join(lines)


def _user_payload(curriculum: Curriculum, spec: ReelSpec, prior: list[ReelSpec], draft: str) -> str:
    prev = prior[-1] if prior else None
    handoff = ""
    if prev and (prev.narration_draft or "").strip():
        handoff = (
            f"\n\nPrevious episode the viewer just watched: {prev.title}\n"
            f"Their last lines:\n{prev.narration_draft.strip()[-400:]}\n"
            "The draft must continue from that state, not start a new film.\n"
        )
    return (
        f"Course topic: {curriculum.topic}\n\n"
        f"Course spine: {getattr(curriculum, 'spine', '') or '(not named)'}\n\n"
        f"Full syllabus map:\n{_curriculum_map(curriculum)}\n"
        f"{handoff}\n"
        f"Episode bible (the whole film's plot):\n"
        + "\n".join(
            f"- {r.reel_id} {r.title}: {getattr(r, 'outline', '') or r.objective}"
            for r in curriculum.reels
        )
        + "\n\n"
        f"Completed earlier lessons (full scripts, in order):\n{_prior_block(prior)}\n\n"
        f"Current lesson: {spec.reel_id} - {spec.title}\n"
        f"Objective: {spec.objective}\n"
        f"Key terms: {', '.join(spec.key_terms)}\n"
        f"Depends on: {', '.join(spec.depends_on) or 'none'}\n\n"
        f"Draft script to check:\n{draft}"
    )


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


def check_coherency(
    curriculum: Curriculum,
    spec: ReelSpec,
    prior: list[ReelSpec],
    draft: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    recorder: Recorder | None = None,
) -> tuple[str, list[str], str]:
    user = _user_payload(curriculum, spec, prior, draft)
    import llm

    text, editor_model = llm.complete(
        COHERENCY_SYSTEM,
        user,
        json_mode=True,
        openai_key_value=openai_key,
        gemini_key_value=gemini_key,
        openai_model_name=openai_model,
        gemini_model_name=gemini_model,
        recorder=recorder,
        step="coherency",
        step_label="Coherency edit",
    )
    data = _parse_json(text)
    script = str(data.get("script") or "").strip() or draft
    changes = [str(c).strip() for c in (data.get("changes") or []) if str(c).strip()]
    return script, changes, editor_model
