from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Callable, Optional

import audio_cache
from coherency import check_coherency
from curriculum import Curriculum, CurriculumStore, ReelSpec, clip_source, course_is_open
from metrics import Recorder, merge_metrics, summarize, timed
from director import plan_reel

logger = logging.getLogger("primer.pipeline")

SCRIPT_SOURCE_LIMIT = 20000

REEL_ANSWER_SYSTEM = """You write the spoken script for ONE short EXPLAINER VIDEO — the Vox / 3Blue1Brown kind, heard once on a phone. You get this lesson's assignment. Write only what this lesson teaches.

WHO IS LISTENING
A person who wants to learn this and keep it. They are smart. They know nothing about this topic yet. They cannot pause. They need to know, in the first breath, WHAT we are talking about — then they need a story they can follow to the end. They are not collecting facts.

NAME THE SUBJECT FIRST
The opening sentence says what we are discussing, in words they already own. Not a syllabus. Not "in this video". A person pointing at the thing:
- Course, first lesson: name the topic as a live question. "We're going to figure out why money that just sits there still grows."
- Course, later lesson: name the same topic AND this step. "We're still with that $100 in the bank. Last time it paid $10. Now we find out why year two pays more."
- Standalone: name the question they asked, as the thing under discussion.
If a stranger overheard only the first sentence, they should know the subject. Then start the story. Do not skip the subject and jump into an unexplained object.

HOW THE STORY IS BUILT
Borrow the structures that actually hold attention in teaching and short explainers — not as labels in the prose, as the order of events.

Pixar / Kenn Adams story spine (the plot):
Once there was a familiar situation. Every day it behaved as you'd expect. Until one day it didn't. Because of that, we watch a specific case. Because of that, the hidden rule shows itself. Until finally we can say the rule in one line. Ever since, the original situation looks different.

STEM "voyage and return" (Akisheva): start in the familiar world → show the anomaly that breaks intuition → walk the investigation through one concrete case → return to the original situation with a new understanding. The name of the concept arrives as the ANSWER, not the opening title.

Explainer causal chain (hook / problem / mechanism / proof / implication):
the subject → why the usual guess fails → what actually changes → the same example proving it → what that means. Do not end on "and that's how X works". End on the implication for the situation you opened with.

Heath SUCCESs, in the writing: Simple (one idea), Unexpected (the anomaly), Concrete (one object, real numbers), Credible (the walkthrough is the proof), Story (cause and effect, not a list).

THE SHAPE OF THIS SCRIPT (always, in this order)
1. SUBJECT — one sentence: what we are discussing.
2. WORLD — the familiar setup. The object as it seems before the idea.
3. PROBLEM — the thing that doesn't fit. The itch. Expected vs actual.
4. INSTANCE — one worked example. Real numbers or a real object, step by step. This is the proof, not a decoration after the lecture.
5. TURN — the mechanism: because THIS happened, THAT happened. Here the term may arrive, after they have seen it.
6. LAND — return to the opening situation. One keepable line. What is now true that wasn't at the start.

All six serve ONE idea — the objective. They are scenes of the same story, not six topics. If you cannot fit a beat, shorten sentences; do not drop SUBJECT, PROBLEM, INSTANCE, or LAND.

ONE OBJECT THE WHOLE WAY
You are given a concrete example. That is the character. Do not switch examples mid-script. Do not add a second analogy. Show the thing happening before you name it.

ATTENTION THROUGH THE MIDDLE
The middle dies when you stop causing things. After each sentence, the object should be in a new state, or the listener should need the next sentence to resolve the last. "Because of that…" is the engine. A stack of true statements with no because is why stories vanish after a good opening.

VOICE
Conversational, like explaining to one friend (Mayer's personalization). Specific. Slightly surprising. Never cute.
Banned: "X is defined as", "there are several types", "in simple terms", "basically", "it's important to note", "in this video", "let's dive in", "have you ever wondered", "imagine a world", "buckle up", recap lists, TED-hype.

PACE
- 100 to 170 words. Long enough for the arc. Short enough to hear once.
- Short sentences. One clause each, mostly.
- Everyday words. A technical term only after the sentence before it has shown the thing.

WHERE THIS LESSON SITS
- FIRST lesson of a course: SUBJECT names the course topic as a question. Then the story of the spine begins from zero.
- MIDDLE: SUBJECT still names what we are discussing (the spine + this step). You are not silent about the topic. Then continue the same object's story from where the last lesson left it. No syllabus preview. No recap of every prior lesson — one callback is enough.
- LAST: SUBJECT names the whole thing we have been after. Finish the object's story. LAND is the conclusion the course was for — not a list of lessons.
- STANDALONE: SUBJECT is their question. Full arc in one script.

USING EARLIER LESSONS
When earlier lessons exist, this is the next chapter of ONE YouTube film, not a new video. Same spine. Same object. The viewer just watched the previous lesson — you are given their last lines. After you name the subject, pick up from that exact state (because of that…). Never a new metaphor. Never re-teach. Never open as if they walked in cold. A script that could play in any order is a failed script. The second half must still be story, not a moral or a definition.

If the user message includes a LIVE WEB BRIEFING, those are the current facts. Teach from them. Name dates. Do not invent news. If the briefing is missing, say you could not confirm it live.

Output: plain prose only. No title, no headings, no beat labels, no bullets, no markdown, no JSON. The six story moves happen in the sentences, not as section headers the listener would hear.
"""


def _world_brief(reel: dict) -> str:
    """One-line-per-object summary of a finished reel's visual vocabulary."""
    world = reel.get("world") or {}
    parts = []
    for obj in (world.get("objects") or [])[:4]:
        label = obj.get("id") or obj.get("what") or ""
        drawn = obj.get("how_drawn") or ""
        if label and drawn:
            parts.append(f"{label}: {drawn}")
    if world.get("accent_means"):
        parts.append(f"accent marks {world['accent_means']}")
    return "; ".join(parts)[:400]


def _prior_visual_world(prior: list[ReelSpec]) -> str:
    lines = [f"- {p.title}: {p.visual_world}" for p in prior if p.visual_world]
    return "\n".join(lines[-3:])


def _prior_specs(curriculum: Curriculum, spec: ReelSpec) -> list[ReelSpec]:
    """Every earlier episode in syllabus order.

    Continuity is the previous YouTube chapter, not only `depends_on`. A later
    lesson that listed only reel_01 would otherwise forget reel_02, which is
    how courses felt random when the learner hit Next.
    """
    priors: list[ReelSpec] = []
    for reel in curriculum.reels:
        if reel.reel_id == spec.reel_id:
            break
        priors.append(reel)
    return priors


def _tail_sentences(text: str, count: int = 2) -> str:
    blob = re.sub(r"\s+", " ", (text or "").strip())
    if not blob:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", blob)
    parts = [p for p in parts if p]
    return " ".join(parts[-count:])


def _last_shot(reel: dict | None) -> str:
    if not reel:
        return ""
    beats = []
    if isinstance(reel.get("hook"), dict):
        beats.append(reel["hook"])
    beats.extend(b for b in (reel.get("scenes") or []) if isinstance(b, dict))
    if isinstance(reel.get("payoff"), dict):
        beats.append(reel["payoff"])
    if not beats:
        return ""
    last = beats[-1]
    heading = ""
    for item in last.get("on_screen") or []:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("content"):
            heading = str(item["content"]).strip()
            break
    narration = str(last.get("narration") or "").strip()
    bits = [p for p in (f"on-screen: {heading}" if heading else "", f"they heard: {narration}" if narration else "") if p]
    return "; ".join(bits)


def lesson_position(curriculum: Curriculum, spec: ReelSpec) -> str:
    """first / last / middle / only — drives the opening framing line and the
    closing convergence line."""
    total = len(curriculum.reels)
    if total <= 1:
        return "only"
    index = next((i for i, r in enumerate(curriculum.reels) if r.reel_id == spec.reel_id), 0)
    if index == 0:
        return "first"
    if index == total - 1:
        return "last"
    return "middle"


def _position_brief(curriculum: Curriculum, position: str) -> str:
    topic = curriculum.topic
    spine = getattr(curriculum, "spine", "") or topic
    if position == "first":
        return (
            f"POSITION: FIRST lesson of this course. First sentence: say we are discussing {topic}, "
            f"as a live question a person would speak. Then tell the story of the spine ({spine}) from zero, "
            "in the full arc: world, problem, instance, turn, land."
        )
    if position == "last":
        arc = "\n".join(f"  {i + 1}. {r.title} — {r.objective}" for i, r in enumerate(curriculum.reels))
        return (
            f"POSITION: LAST lesson. First sentence: we are still discussing {topic} — the thing the "
            f"whole course was after. You are the final chapter of one film: pick up from the previous "
            f"lesson's last lines, then finish the spine ({spine}). Full arc. LAND is the conclusion, "
            f"not a recap list. The course covered, in order:\n{arc}"
        )
    if position == "only":
        return (
            "POSITION: STANDALONE. First sentence names what we are discussing (their question). "
            "Then the full story arc: world, problem, instance, turn, land."
        )
    return (
        f"POSITION: MIDDLE lesson. First sentence still names what we are discussing: {topic}, "
        f"and this step of the spine ({spine}). The NEXT sentence must continue from where the last "
        "lesson left the object (you are given those last lines). Then this chapter's problem, "
        "instance, turn, and land. Do not skip the subject. Do not start a new example. "
        "Do not vanish into a definition in the second half. Do not open as a standalone video."
    )


def _answer_prompt(
    curriculum: Curriculum,
    spec: ReelSpec,
    prior: list[ReelSpec],
    retry_note: str,
    position: str = "middle",
) -> str:
    prior_block = (
        "\n".join(f"- {p.title} (established: {', '.join(p.key_terms)})" for p in prior)
        or "(none - this is the first lesson, assume zero prior context)"
    )
    story_so_far = "\n\n".join(
        f"### {p.title}\n{p.narration_draft.strip()}"
        for p in prior
        if (p.narration_draft or "").strip()
    )
    lines = [
        f"Course topic: {curriculum.topic}",
        f"Course spine (every lesson continues THIS, not a new metaphor): {getattr(curriculum, 'spine', '') or spec.example or curriculum.topic}",
        f"This lesson's title: {spec.title}",
        f"This lesson's objective (the ONE idea): {spec.objective}",
        f"The concrete example to build the script around: {spec.example or '(none given — pick one specific everyday example and walk through it)'}",
        f"Key terms to use: {', '.join(spec.key_terms)}",
        f"Earlier lessons this one may build on:\n{prior_block}",
        _position_brief(curriculum, position),
    ]
    bible = "\n".join(
        f"- {r.title}: {r.outline}"
        for r in curriculum.reels
        if (getattr(r, "outline", "") or "").strip()
    )
    if bible:
        lines.append(
            "EPISODE BIBLE (the whole film's plot, decided before any script). "
            "Stay on this even if a neighboring script is not written yet:\n"
            + bible
        )
    source = clip_source(getattr(curriculum, "source_text", "") or "", SCRIPT_SOURCE_LIMIT)
    if source:
        lines.append(
            "SOURCE DOCUMENT — this course teaches THIS text, not generic knowledge. "
            "Stay faithful. Use its numbers, names, and examples. Cover this lesson's "
            "slice of the important ideas. Do not invent claims that are not in the source. "
            "You may simplify and reorder for teaching.\n\n"
            f"{source}"
        )
    prev = prior[-1] if prior else None
    if prev and (prev.narration_draft or "").strip():
        lines.append(
            f"THE VIEWER JUST FINISHED \"{prev.title}\". Their last lines were:\n"
            f"{_tail_sentences(prev.narration_draft)}\n"
            "Your second sentence continues from THAT state of the same object. "
            "Do not skip a chapter. Do not swap in a new everyday example."
        )
    if story_so_far:
        lines.append(
            "THE COURSE STORY SO FAR (full scripts the learner already heard, in order). "
            "Write the next chapter of this one film, keeping the same object in motion:\n"
            + story_so_far
        )
    lines.append(
        "Write the script now. 100-170 words. First sentence names what we are discussing. "
        "Then pick up the previous episode if there is one. Then story: world, problem, "
        "instance, turn, land. Stay on the spine. One idea."
    )
    if retry_note:
        lines.append(retry_note)
    return "\n\n".join(lines)


def generate_standalone_script(
    question: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> tuple[str, str]:
    """Script Agent for a single test question (no course syllabus)."""
    user = (
        f"Course topic: {question.strip()}\n"
        f"This lesson's title: {question.strip()[:72]}\n"
        f"This lesson's objective (the ONE idea): {question.strip()}\n"
        "The concrete example to build the script around: (pick one specific everyday example "
        "with real numbers or a real object and walk through it)\n"
        "Key terms to use: (derive two or three from the question; introduce them only after showing them)\n"
        "Earlier lessons this one may build on:\n(none - this is a standalone explainer, assume zero prior context)\n\n"
        "POSITION: STANDALONE. First sentence names what we are discussing (their question). "
        "Then the full story: familiar world, problem, worked instance, mechanism, landing line.\n\n"
        "Write the script now. 100-170 words. One idea. Make it a story they can follow."
    )
    import llm

    return llm.complete(
        REEL_ANSWER_SYSTEM,
        user,
        openai_key_value=openai_key,
        gemini_key_value=gemini_key,
        openai_model_name=openai_model,
        gemini_model_name=gemini_model,
        step="script",
        step_label="Standalone script",
    )


def generate_answer_for_reel(
    curriculum: Curriculum,
    spec: ReelSpec,
    prior: list[ReelSpec],
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    retry_note: str = "",
    position: str = "middle",
    recorder: Recorder | None = None,
) -> tuple[str, str]:
    user = _answer_prompt(curriculum, spec, prior, retry_note, position)
    look = " ".join(
        part
        for part in (curriculum.topic, spec.title, spec.objective, spec.example, spec.narration_draft)
        if part
    )
    if not (getattr(curriculum, "source_text", "") or "").strip():
        try:
            import web_brief

            user = web_brief.attach(user, web_brief.fetch_brief(look, openai_key, gemini_key))
        except Exception:
            pass
    import llm

    text, label = llm.complete(
        REEL_ANSWER_SYSTEM,
        user,
        openai_key_value=openai_key,
        gemini_key_value=gemini_key,
        openai_model_name=openai_model,
        gemini_model_name=gemini_model,
        recorder=recorder,
        step="script",
        step_label="Script writing",
    )
    return text, label


def write_script_for_spec(
    curriculum: Curriculum,
    spec: ReelSpec,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    retry_note: str = "",
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> None:
    """Script + coherency only. Later lessons wait on this so they can stay continuous."""
    prior = _prior_specs(curriculum, spec)
    recorder = Recorder()
    phase = timed()
    phase.__enter__()
    draft, draft_model = generate_answer_for_reel(
        curriculum,
        spec,
        prior,
        openai_key,
        gemini_key,
        answer_openai_model,
        answer_gemini_model,
        retry_note,
        position=lesson_position(curriculum, spec),
        recorder=recorder,
    )
    if not draft:
        raise RuntimeError("answer agent returned no text")
    if on_progress:
        on_progress("Checking it follows the last lesson", 0.1)
    script, changes, editor_model = check_coherency(
        curriculum, spec, prior, draft, openai_key, gemini_key, answer_openai_model,
        answer_gemini_model, recorder,
    )
    phase.__exit__()
    if changes:
        logger.info("coherency edits for %s (%s): %s", spec.reel_id, spec.title, "; ".join(changes))
    spec.narration_draft = script
    spec.coherency_notes = changes
    spec.metrics = merge_metrics(
        spec.metrics or {},
        summarize(recorder.dump(), scripted_seconds=round(phase.seconds, 3)),
    )
    if changes and editor_model:
        spec.script_model = f"{draft_model} → coherency {editor_model}"
    else:
        spec.script_model = draft_model


def _director_prior_story(curriculum: Curriculum, spec: ReelSpec, prior: list[ReelSpec]) -> str:
    """What the director needs to continue one film instead of a new slideshow."""
    if not prior:
        return ""
    total = len(curriculum.reels)
    index = next((i for i, r in enumerate(curriculum.reels) if r.reel_id == spec.reel_id), 0)
    spine = getattr(curriculum, "spine", "") or curriculum.topic
    parts = [
        f"SERIES: this is episode {index + 1} of {total} on {curriculum.topic}.",
        f"Spine (never replace this object): {spine}",
        "Direct the NEXT episode of one YouTube film. The hook continues the previous "
        "episode's last state. Do not open as a cold start. Do not invent a new metaphor.",
    ]
    prev = prior[-1]
    parts.append(f"PREVIOUS EPISODE: {prev.title}\n{prev.narration_draft.strip()[:1200]}")
    last_lines = _tail_sentences(prev.narration_draft)
    if last_lines:
        parts.append(f"THEY LAST HEARD:\n{last_lines}\nHook narration picks up from here.")
    shot = _last_shot(prev.reel)
    if shot:
        parts.append(f"THEY LAST SAW: {shot}")
    visual = _prior_visual_world(prior)
    if visual:
        parts.append(f"REUSE these drawings rather than new icons:\n{visual}")
    if len(prior) > 1:
        earlier = "\n".join(
            f"- {p.title}: {_tail_sentences(p.narration_draft, 1) or p.objective}"
            for p in prior[:-1]
        )
        parts.append(f"EARLIER EPISODES:\n{earlier}")
    return "\n\n".join(parts)


def direct_spec(
    curriculum: Curriculum,
    spec: ReelSpec,
    openai_key: str,
    gemini_key: str,
    director_openai_model: str,
    director_gemini_model: str,
    visual_engine: str = "svg",
    on_progress=None,
) -> None:
    script = (spec.narration_draft or "").strip()
    if not script:
        raise RuntimeError("director ran with no script")
    prior = _prior_specs(curriculum, spec)
    reel = plan_reel(
        spec.objective,
        script,
        spec.title,
        openai_key,
        gemini_key,
        director_openai_model,
        director_gemini_model,
        prior_world=_director_prior_story(curriculum, spec, prior),
        script_model=spec.script_model,
        position=lesson_position(curriculum, spec),
        visual_engine=visual_engine,
        on_progress=on_progress,
    )
    if not reel:
        raise RuntimeError("director failed to produce a reel")
    spec.metrics = merge_metrics(spec.metrics or {}, reel.get("metrics") or {})
    # Generate this lesson's narration now, while the learner is still on an
    # earlier lesson, so advancing to it starts instantly.
    audio_cache.warm_reel_in_background(
        reel,
        os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-athena-en"),
        os.getenv("DEEPGRAM_API_KEY", "").strip(),
    )
    spec.visual_world = _world_brief(reel)
    spec.reel = reel
    spec.status = "ready"
    spec.error = ""


def generate_reel_for_spec(
    curriculum: Curriculum,
    spec: ReelSpec,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
    retry_note: str = "",
    visual_engine: str = "svg",
) -> None:
    """Full lesson for the waiting viewer: script, then director/SVG or Sora."""
    write_script_for_spec(
        curriculum,
        spec,
        openai_key,
        gemini_key,
        answer_openai_model,
        answer_gemini_model,
        retry_note,
    )
    direct_spec(
        curriculum,
        spec,
        openai_key,
        gemini_key,
        director_openai_model,
        director_gemini_model,
        visual_engine=visual_engine,
    )


def generate_reel_1_sync(
    curriculum: Curriculum,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
    visual_engine: str = "svg",
) -> Curriculum:
    store = CurriculumStore.default()
    spec = curriculum.reels[0]
    spec.status = "generating"
    curriculum.status = "generating"
    store.save(curriculum)
    try:
        generate_reel_for_spec(
            curriculum,
            spec,
            openai_key,
            gemini_key,
            answer_openai_model,
            answer_gemini_model,
            director_openai_model,
            director_gemini_model,
            visual_engine=visual_engine,
        )
    except Exception as exc:
        spec.status = "failed"
        spec.error = str(exc)
        store.save(curriculum)
        raise
    store.save(curriculum)
    return curriculum


_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()
_STOP: set[str] = set()
_STOP_LOCK = threading.Lock()

# Two courses may draw at once; the rest wait and start by themselves when a
# slot frees. The old rule (one course, silently defer the rest) left later
# courses stuck at "generating" forever unless the learner kept the page open.
MAX_ACTIVE_COURSES = 2
# After a restart, only pick courses back up automatically if they were being
# worked on recently — an abandoned course from last week should not start
# spending tokens just because the server rebooted.
AUTO_RESUME_WINDOW_S = 6 * 3600

# Where the lesson's progress bar sits at each hand-off.
SCRIPTED_AT = 0.15
DIRECT_SPAN = 0.83


def _set_progress(curriculum_id: str, reel_id: str, stage: str, label: str, progress: float, **extra) -> None:
    if stop_requested(curriculum_id):
        return
    CurriculumStore.default().update_reel(
        curriculum_id,
        reel_id,
        stage=stage,
        stage_label=label,
        progress=round(max(0.0, min(1.0, progress)), 3),
        updated_at=time.time(),
        **extra,
    )


def stop_requested(curriculum_id: str) -> bool:
    with _STOP_LOCK:
        return curriculum_id in _STOP


def request_stop(curriculum_id: str):
    """Stop starting new LLM work. In-flight calls may finish; their results are dropped."""
    with _STOP_LOCK:
        _STOP.add(curriculum_id)
    stopped = CurriculumStore.default().fail_unfinished(curriculum_id)
    with _RUNNING_LOCK:
        running = curriculum_id in _RUNNING
    if not running:
        # Nothing will consume the flag, and a stale flag blocks a later resume.
        with _STOP_LOCK:
            _STOP.discard(curriculum_id)
    return stopped


def resume_course(curriculum_id: str):
    """Retry every failed or stopped lesson. Finished lessons and scripts are kept."""
    with _STOP_LOCK:
        _STOP.discard(curriculum_id)
    store = CurriculumStore.default()
    curriculum = store.reopen_failed(curriculum_id)
    if not curriculum:
        return None
    _launch(curriculum)
    return store.get(curriculum_id)


def _launch(curriculum: Curriculum) -> None:
    """Start (or queue) a course with the keys and models from the environment."""
    import llm

    if not llm.has_any_key():
        return
    start_background_reels(
        curriculum.curriculum_id,
        llm.openai_key(),
        llm.gemini_key(),
        llm.openai_model(),
        llm.gemini_model(),
        llm.director_openai_model(),
        llm.director_gemini_model(),
        visual_engine=curriculum.visual_engine or "svg",
        provider=curriculum.provider or "auto",
    )


def _last_touched(curriculum: Curriculum) -> float:
    try:
        written = CurriculumStore.default()._path(curriculum.curriculum_id).stat().st_mtime
    except OSError:
        written = 0.0
    return max([curriculum.created_at, written, *(r.updated_at for r in curriculum.reels)])


def _start_next_waiting() -> None:
    """A slot freed up: start the newest course that is still open."""
    store = CurriculumStore.default()
    with _RUNNING_LOCK:
        running = set(_RUNNING)
    waiting = [
        item
        for item in store.list()
        if course_is_open(item)
        and item.curriculum_id not in running
        and not stop_requested(item.curriculum_id)
        and item.note
    ]
    waiting.sort(key=lambda item: item.created_at, reverse=True)
    for item in waiting:
        _launch(item)


def resume_all() -> None:
    """Server start: pick up courses a reload or crash cut off mid-generation."""
    store = CurriculumStore.default()
    cutoff = time.time() - AUTO_RESUME_WINDOW_S
    open_courses = [
        item for item in store.list() if course_is_open(item) and _last_touched(item) >= cutoff
    ]
    open_courses.sort(key=lambda item: item.created_at, reverse=True)
    for item in open_courses:
        logger.info("resuming course %s after restart", item.curriculum_id)
        _launch(item)


def start_background_reels(
    curriculum_id: str,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
    visual_engine: str = "svg",
    provider: str = "auto",
) -> None:
    """Script lesson 1, then draw it while later scripts (and their SVGs) run in parallel."""
    if stop_requested(curriculum_id):
        CurriculumStore.default().fail_unfinished(curriculum_id)
        with _STOP_LOCK:
            _STOP.discard(curriculum_id)
        return
    store = CurriculumStore.default()
    with _RUNNING_LOCK:
        if curriculum_id in _RUNNING:
            return
        queued = len(_RUNNING) >= MAX_ACTIVE_COURSES
        if not queued:
            _RUNNING.add(curriculum_id)
    if queued:
        logger.info("queue %s; %s courses already drawing", curriculum_id, MAX_ACTIVE_COURSES)
        store.update_note(
            curriculum_id,
            "Waiting for another course to finish drawing — this one starts automatically.",
        )
        return
    store.update_note(curriculum_id, "")
    thread = threading.Thread(
        target=_run_queue,
        args=(
            curriculum_id,
            openai_key,
            gemini_key,
            answer_openai_model,
            answer_gemini_model,
            director_openai_model,
            director_gemini_model,
            visual_engine,
            provider,
        ),
        daemon=True,
    )
    thread.start()


def resume_if_needed(curriculum_id: str) -> None:
    """If the process restarted mid-course, pick the queue back up on the next poll."""
    if stop_requested(curriculum_id):
        return
    curriculum = CurriculumStore.default().get(curriculum_id)
    if not curriculum or not course_is_open(curriculum):
        return
    with _RUNNING_LOCK:
        if curriculum_id in _RUNNING:
            return
    logger.info("resume stalled curriculum %s", curriculum_id)
    _launch(curriculum)


# Scripts stay serial (later lessons read prior narration). Directing waits for
# the previous episode so visual_world / last-shot continuity is real. Scripts
# still run ahead, overlapping the current draw. Each director already fans out
# missing beats, so keep this at 1 in-flight episode.
MAX_DIRECT_WORKERS = 1
CLAUDE_DIRECT_WORKERS = 1


def _prior_episode_settled(curriculum: Curriculum, spec: ReelSpec) -> bool:
    idx = next((i for i, r in enumerate(curriculum.reels) if r.reel_id == spec.reel_id), 0)
    if idx <= 0:
        return True
    return curriculum.reels[idx - 1].status in {"ready", "failed"}


def _run_queue(
    curriculum_id: str,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
    visual_engine: str = "svg",
    provider: str = "auto",
) -> None:
    """Script one lesson ahead of the current draw; keep drawing in this process."""
    import llm

    llm.prefer(provider)
    store = CurriculumStore.default()
    store.update_status(curriculum_id, "generating")
    directs_n = CLAUDE_DIRECT_WORKERS if provider == "claude" else MAX_DIRECT_WORKERS
    direct_pool = ThreadPoolExecutor(max_workers=directs_n)
    directs: dict[str, Future] = {}
    direct_tries: dict[str, int] = {}

    def in_flight_ids() -> set[str]:
        return {rid for rid, fut in directs.items() if not fut.done()}

    def kick_direct(reel_id: str) -> None:
        fut = directs.get(reel_id)
        if fut is not None and not fut.done():
            return
        if stop_requested(curriculum_id):
            return
        direct_tries[reel_id] = direct_tries.get(reel_id, 0) + 1
        directs[reel_id] = direct_pool.submit(
            _direct_one_with_retry,
            curriculum_id,
            reel_id,
            openai_key,
            gemini_key,
            director_openai_model,
            director_gemini_model,
            visual_engine,
            provider,
        )

    def submit_ready_directs() -> None:
        cur = store.get(curriculum_id)
        if not cur:
            return
        for spec in cur.reels:
            if spec.status == "failed":
                continue
            if spec.reel and spec.status == "ready":
                continue
            if not (spec.narration_draft or "").strip():
                continue
            if not _prior_episode_settled(cur, spec):
                continue
            fut = directs.get(spec.reel_id)
            if fut is not None and not fut.done():
                continue
            if fut is not None and fut.done():
                if spec.status in {"ready", "failed"}:
                    continue
                if direct_tries.get(spec.reel_id, 0) >= 2:
                    store.update_reel(
                        curriculum_id,
                        spec.reel_id,
                        status="failed",
                        error="director stalled after retries",
                    )
                    continue
                directs.pop(spec.reel_id, None)
            logger.info("directing %s (%s)", spec.reel_id, spec.title)
            kick_direct(spec.reel_id)

    idle_spins = 0
    try:
        while True:
            if stop_requested(curriculum_id):
                return
            curriculum = store.get(curriculum_id)
            if not curriculum:
                return

            submit_ready_directs()

            next_script = None
            for spec in curriculum.reels:
                if spec.status == "failed":
                    continue
                if (spec.narration_draft or "").strip():
                    continue
                next_script = spec
                break

            pending = [fut for fut in directs.values() if not fut.done()]
            flying = in_flight_ids()
            # Lessons that already have a script but are not the one currently drawing.
            # Keep this at 0 or 1 so we overlap script+draw without starving SVG calls.
            ahead = [
                spec
                for spec in curriculum.reels
                if (spec.narration_draft or "").strip()
                and spec.status not in {"ready", "failed"}
                and spec.reel_id not in flying
            ]
            if next_script and len(ahead) < 1:
                logger.info("writing %s (%s)", next_script.reel_id, next_script.title)
                _script_one_with_retry(
                    curriculum_id,
                    next_script.reel_id,
                    openai_key,
                    gemini_key,
                    answer_openai_model,
                    answer_gemini_model,
                    provider,
                )
                submit_ready_directs()
                idle_spins = 0
                continue

            if pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    try:
                        fut.result()
                    except Exception as exc:
                        logger.warning("direct worker failed: %s", exc)
                idle_spins = 0
                continue

            leftover = store.get(curriculum_id)
            still_open = leftover and any(
                r.status not in {"ready", "failed"} for r in leftover.reels
            )
            if still_open:
                idle_spins += 1
                if idle_spins > 80:
                    for spec in leftover.reels:
                        if spec.status not in {"ready", "failed"}:
                            store.update_reel(
                                curriculum_id,
                                spec.reel_id,
                                status="failed",
                                error="generation stalled",
                                stage="failed",
                                stage_label="Stalled — press Retry",
                                updated_at=time.time(),
                            )
                    break
                time.sleep(0.4)
                continue
            break

        _finalize(curriculum_id)
    finally:
        direct_pool.shutdown(wait=True, cancel_futures=False)
        if stop_requested(curriculum_id):
            store.fail_unfinished(curriculum_id)
        with _STOP_LOCK:
            _STOP.discard(curriculum_id)
        with _RUNNING_LOCK:
            _RUNNING.discard(curriculum_id)
        try:
            _start_next_waiting()
        except Exception:
            logger.exception("could not start the next queued course")


def _script_one_with_retry(
    curriculum_id: str,
    reel_id: str,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    provider: str = "auto",
) -> None:
    import llm

    llm.prefer(provider)
    if stop_requested(curriculum_id):
        return
    store = CurriculumStore.default()
    store.update_reel(curriculum_id, reel_id, status="generating", started_at=time.time())
    _set_progress(curriculum_id, reel_id, "script", "Writing the script", 0.03)

    def script_progress(label: str, fraction: float) -> None:
        _set_progress(curriculum_id, reel_id, "script", label, fraction)

    last_error = ""
    for attempt in (1, 2):
        if stop_requested(curriculum_id):
            return
        curriculum = store.get(curriculum_id)
        if not curriculum:
            return
        spec = curriculum.reel_by_id(reel_id)
        if not spec:
            return
        retry_note = (
            f"Your previous attempt failed, avoid the same mistake: {last_error}"
            if attempt == 2 and last_error
            else ""
        )
        try:
            write_script_for_spec(
                curriculum,
                spec,
                openai_key,
                gemini_key,
                answer_openai_model,
                answer_gemini_model,
                retry_note=retry_note,
                on_progress=script_progress,
            )
            if stop_requested(curriculum_id):
                return
            store.update_reel(
                curriculum_id,
                reel_id,
                stage="waiting",
                stage_label="Script ready · waiting for the previous lesson to be drawn",
                progress=SCRIPTED_AT,
                updated_at=time.time(),
                status="scripted",
                attempt=attempt,
                error="",
                narration_draft=spec.narration_draft,
                coherency_notes=spec.coherency_notes,
                # update_reel writes only the named fields — omitting this
                # silently discarded the script phase's timing and tokens.
                metrics=spec.metrics,
            )
            logger.info("script ready for %s (%s)", reel_id, spec.title)
            return
        except Exception as exc:
            last_error = str(exc)
            logger.warning("script %s attempt %s failed: %s", reel_id, attempt, exc)
            store.update_reel(
                curriculum_id,
                reel_id,
                status="failed" if attempt == 2 else "generating",
                attempt=attempt,
                error=last_error,
                stage="failed" if attempt == 2 else "script",
                stage_label="Script failed" if attempt == 2 else "Script hit a snag · retrying",
                updated_at=time.time(),
            )


def _direct_one_with_retry(
    curriculum_id: str,
    reel_id: str,
    openai_key: str,
    gemini_key: str,
    director_openai_model: str,
    director_gemini_model: str,
    visual_engine: str = "svg",
    provider: str = "auto",
) -> None:
    import llm

    llm.prefer(provider)
    if stop_requested(curriculum_id):
        return
    store = CurriculumStore.default()
    store.update_reel(curriculum_id, reel_id, status="generating")
    _set_progress(curriculum_id, reel_id, "storyboard", "Planning the shots", SCRIPTED_AT + 0.01)

    def direct_progress(stage: str, label: str, fraction: float) -> None:
        _set_progress(curriculum_id, reel_id, stage, label, SCRIPTED_AT + DIRECT_SPAN * fraction)

    last_error = ""
    for attempt in (1, 2):
        if stop_requested(curriculum_id):
            return
        curriculum = store.get(curriculum_id)
        if not curriculum:
            return
        spec = curriculum.reel_by_id(reel_id)
        if not spec:
            return
        try:
            direct_spec(
                curriculum,
                spec,
                openai_key,
                gemini_key,
                director_openai_model,
                director_gemini_model,
                visual_engine=visual_engine,
                on_progress=direct_progress,
            )
            if stop_requested(curriculum_id):
                return
            store.update_reel(
                curriculum_id,
                reel_id,
                stage="ready",
                stage_label="Ready",
                progress=1.0,
                updated_at=time.time(),
                status="ready",
                attempt=attempt,
                error="",
                visual_world=spec.visual_world,
                reel=spec.reel,
                metrics=spec.metrics,
            )
            logger.info("reel directed for %s (%s)", reel_id, spec.title)
            import video_export

            index = next((i for i, r in enumerate(curriculum.reels) if r.reel_id == reel_id), 0)
            video_export.prepare_in_background(
                f"{curriculum.topic} — {index + 1}. {spec.title}", [spec.reel]
            )
            return
        except Exception as exc:
            last_error = str(exc)
            logger.warning("director %s attempt %s failed: %s", reel_id, attempt, exc)
            store.update_reel(
                curriculum_id,
                reel_id,
                status="failed" if attempt == 2 else "generating",
                attempt=attempt,
                error=last_error,
                stage="failed" if attempt == 2 else "storyboard",
                stage_label="Drawing failed" if attempt == 2 else "Drawing hit a snag · retrying",
                updated_at=time.time(),
            )


def _finalize(curriculum_id: str) -> None:
    store = CurriculumStore.default()
    curriculum = store.get(curriculum_id)
    if not curriculum:
        return
    if any(r.status not in {"ready", "failed"} for r in curriculum.reels):
        return
    store.update_status(
        curriculum_id,
        "ready" if curriculum.reels[0].status == "ready" else "failed",
    )
    ready = [r.reel for r in curriculum.reels if r.status == "ready" and r.reel]
    if len(ready) > 1:
        import video_export

        video_export.prepare_in_background(curriculum.topic, ready)
