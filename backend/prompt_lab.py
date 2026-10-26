"""Prompt Evolution Lab: selector, critic, optimizer, and the loop controller.

The LLM never owns the loop. This module starts a daemon thread (same pattern
as curriculum background generation), persists after every step, and decides
stop / keep / reject in code.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time
import uuid
from typing import Optional

from openai import OpenAI
from google import genai
from google.genai import types

from director import plan_reel
from pipeline import generate_standalone_script
from prompt_lab_store import (
    DEFAULT_IMPROVEMENT_THRESHOLD,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_STAGNANT_ITERS,
    HARD_MAX_ITERATIONS,
    ITERATION_RETRY_LIMIT,
    JOB_TIMEOUT_SECONDS,
    MAX_CONCURRENT_RUNS,
    EvolutionIteration,
    EvolutionRun,
    PromptLabStore,
    PromptSet,
    QuestionResult,
    SCORE_KEYS,
    clamp_max_iterations,
    mean_overall,
    should_stop_run,
)

logger = logging.getLogger("primer.prompt_lab")

_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()

SELECTOR_SYSTEM = """You pick which Director prompt should control a Primer explainer reel.

You receive a test question and a list of specialized prompt topics (the generic fallback is NOT in the list).

Return JSON only:
{
  "set_id": "the id of the best specialized match, or empty string if none fit",
  "reason": "one sentence a human can read",
  "confidence": 0.0
}

Rules:
- Prefer an exact or strong topical match (calculus, finance, biology, ...).
- If the question is abstract, cross-cutting, or matches nothing well, return set_id "".
- Do not invent a set_id. Copy one from the list or use "".
- confidence is 0-1. Below 0.45 means you should return "".
"""

CRITIC_SYSTEM = """You are the Prompt Lab critic for Primer explainer reels.

You score how well a directed reel teaches a curious phone viewer who hears the narration once.
You do NOT rewrite the reel. You do NOT decide whether the evolution loop continues.

Score each dimension 0-10 (decimals allowed). Be strict: 7 is solid, 9 is rare, 10 is almost never.

Dimensions:
- teaching_quality: did one idea actually land?
- coherence: do beats follow as one argument?
- visual_continuity: same visual world transforming, not a new diagram every beat?
- entertainment: would someone keep watching?
- pacing: one step per beat, no cram, no dead air in the writing?
- originality: specific to this question, not a generic template?
- visual_clarity: readable on a phone, few objects, no clutter?
- narration_quality: short, concrete, spoken once?
- phone_readability: headings 2-6 words, diagrams simple?
- overall_score: not an average of vanity — the viewer's actual experience.

Also list:
- strengths (what to preserve)
- weaknesses (what is actually wrong)
- failure_modes (recurring patterns, e.g. "visual world resets every beat")
- recommendations (targeted, testable changes to the DIRECTOR PROMPT, not to this one reel)
- meaningful_remaining_improvement: true if a prompt change could still move the score
- remaining_improvement_evidence: the specific pattern that would have to change

JSON only:
{
  "teaching_quality": 0,
  "coherence": 0,
  "visual_continuity": 0,
  "entertainment": 0,
  "pacing": 0,
  "originality": 0,
  "visual_clarity": 0,
  "narration_quality": 0,
  "phone_readability": 0,
  "overall_score": 0,
  "strengths": [],
  "weaknesses": [],
  "failure_modes": [],
  "recommendations": [],
  "meaningful_remaining_improvement": true,
  "remaining_improvement_evidence": ""
}
"""

OPTIMIZER_SYSTEM = """You are the Prompt Optimizer for Primer's Director.

Your job is a TARGETED edit to the Director system prompt, not a rewrite.

You receive the current prompt, critic scores, failure modes, and recent history.
The generation pipeline will keep using this prompt as the storyboard director.

Rules:
- Do NOT rewrite the entire prompt unless it is incoherent.
- Preserve instructions that the critic marked as strengths.
- Add or tighten 1-3 rules that attack the named failure modes.
- Never drop the JSON storyboard contract (world, beats, keeps/changes, payoff).
- Do not add meta-commentary like "you are an AI".
- Do not try to spawn another optimization loop.

Return JSON only:
{
  "diagnosis": "what is systematically weak in the DIRECTOR, in one short paragraph",
  "proposed_changes": ["bullet of a specific addition or deletion"],
  "reasoning": "why these changes, tied to failure modes",
  "expected_impact": "which scores should move and why",
  "new_prompt": "the full prompt text to use next"
}
"""


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


def _complete_json(
    system: str,
    user: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> dict:
    if openai_key:
        client = OpenAI(api_key=openai_key)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for kwargs in (
            {"model": openai_model, "messages": messages, "response_format": {"type": "json_object"}},
            {"model": openai_model, "messages": messages},
        ):
            try:
                response = client.chat.completions.create(**kwargs)
                data = _parse_json(response.choices[0].message.content or "")
                if data:
                    return data
            except Exception:
                continue
    if gemini_key:
        try:
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model=gemini_model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                ),
            )
            return _parse_json(response.text or "")
        except Exception:
            return {}
    return {}


def _tokens(text: str) -> set[str]:
    return {part for part in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(part) > 2}


def keyword_select(question: str, sets: list[PromptSet]) -> tuple[Optional[PromptSet], str, bool]:
    q = (question or "").strip().lower()
    q_tokens = _tokens(question)
    specialized = [item for item in sets if item.active and not item.is_generic]
    generic = next((item for item in sets if item.is_generic and item.active), None)

    for item in specialized:
        names = {item.topic.lower(), item.slug.lower(), *{t.lower() for t in item.tags}}
        for name in names:
            if name and (name == q or re.search(rf"\b{re.escape(name)}\b", q)):
                return item, f"Question strongly matches {item.topic} topic.", False

    best: Optional[PromptSet] = None
    best_score = 0.0
    for item in specialized:
        hay = _tokens(f"{item.topic} {item.description} {' '.join(item.tags)}")
        if not hay or not q_tokens:
            continue
        overlap = len(q_tokens & hay) / max(1, len(hay))
        extra = len(q_tokens & hay) / max(1, len(q_tokens))
        score = overlap + extra
        if score > best_score:
            best_score = score
            best = item
    if best and best_score >= 0.22:
        return best, f"Semantic overlap with {best.topic} ({best_score:.2f}).", False
    if generic:
        return generic, "No specialized prompt matched this topic.", True
    if specialized:
        return specialized[0], "No generic prompt exists; used first specialized set.", False
    return None, "No prompts in the library.", True


def select_prompt(
    question: str,
    store: PromptLabStore,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    force_set_id: str = "",
) -> tuple[PromptSet, str, bool]:
    sets = store.list_sets()
    if force_set_id:
        forced = next((s for s in sets if s.set_id == force_set_id), None)
        if forced:
            return forced, f"Manually selected {forced.topic}.", forced.is_generic
    picked, reason, fallback = keyword_select(question, sets)
    if picked and not fallback:
        return picked, reason, False

    specialized = [s for s in sets if s.active and not s.is_generic]
    generic = next((s for s in sets if s.is_generic and s.active), picked)
    if specialized and (openai_key or gemini_key):
        catalog = [{"set_id": s.set_id, "topic": s.topic, "description": s.description, "tags": s.tags} for s in specialized]
        data = _complete_json(
            SELECTOR_SYSTEM,
            f"Question:\n{question}\n\nSpecialized prompts:\n{json.dumps(catalog, ensure_ascii=False)}",
            openai_key,
            gemini_key,
            openai_model,
            gemini_model,
        )
        match_id = str(data.get("set_id") or "").strip()
        confidence = float(data.get("confidence") or 0)
        chosen = next((s for s in specialized if s.set_id == match_id), None)
        if chosen and confidence >= 0.45:
            why = str(data.get("reason") or "").strip() or f"Selector matched {chosen.topic}."
            return chosen, why, False
        llm_reason = str(data.get("reason") or "").strip()
        if generic:
            return generic, llm_reason or reason, True
    if generic:
        return generic, reason, True
    raise RuntimeError("Prompt library is empty.")


def reel_brief(reel: dict) -> dict:
    beats = []
    for beat in [reel.get("hook"), *(reel.get("scenes") or []), reel.get("payoff")]:
        if not isinstance(beat, dict):
            continue
        heading = ""
        has_svg = False
        for item in beat.get("on_screen") or []:
            if item.get("type") in {"text", "stat"} and not heading:
                heading = str(item.get("content") or "")
            if item.get("type") == "svg" and item.get("content"):
                has_svg = True
        visual = beat.get("visual") or {}
        beats.append(
            {
                "role": beat.get("purpose") or beat.get("scene_id"),
                "heading": heading,
                "narration": beat.get("narration") or "",
                "sees": visual.get("sees") or "",
                "keeps": visual.get("keeps") or "",
                "changes": visual.get("changes") or "",
                "has_diagram": has_svg,
            }
        )
    world = reel.get("world") or {}
    return {"title": reel.get("title") or "", "world": world, "beats": beats}


def _clip_score(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(max(0.0, min(10.0, number)), 2)


def normalize_critique(raw: dict) -> dict:
    scores = {key: _clip_score(raw.get(key)) for key in SCORE_KEYS}
    if not scores["overall_score"]:
        others = [scores[k] for k in SCORE_KEYS if k != "overall_score"]
        scores["overall_score"] = round(sum(others) / max(1, len(others)), 2)
    def _list(key: str) -> list[str]:
        values = raw.get(key) or []
        if not isinstance(values, list):
            values = [values]
        return [str(v).strip() for v in values if str(v).strip()][:8]
    return {
        **scores,
        "strengths": _list("strengths"),
        "weaknesses": _list("weaknesses"),
        "failure_modes": _list("failure_modes"),
        "recommendations": _list("recommendations"),
        "meaningful_remaining_improvement": bool(raw.get("meaningful_remaining_improvement", True)),
        "remaining_improvement_evidence": str(raw.get("remaining_improvement_evidence") or "").strip(),
    }


def critique_reel(
    question: str,
    script: str,
    reel: dict,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> dict:
    user = (
        f"Test question:\n{question}\n\n"
        f"Script the director received:\n{script[:4000]}\n\n"
        f"Directed reel (diagrams summarized, no SVG markup):\n"
        f"{json.dumps(reel_brief(reel), ensure_ascii=False)[:12000]}"
    )
    data = _complete_json(CRITIC_SYSTEM, user, openai_key, gemini_key, openai_model, gemini_model)
    if not data:
        raise RuntimeError("critic returned no JSON")
    return normalize_critique(data)


def optimize_prompt(
    current_prompt: str,
    critique_summary: dict,
    history: list[dict],
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> dict:
    user = (
        f"CURRENT PROMPT:\n{current_prompt[:14000]}\n\n"
        f"CRITIC SUMMARY:\n{json.dumps(critique_summary, ensure_ascii=False)[:8000]}\n\n"
        f"ITERATION HISTORY (scores only):\n{json.dumps(history, ensure_ascii=False)[:4000]}\n\n"
        "Propose a targeted edit. Return the FULL new prompt."
    )
    data = _complete_json(OPTIMIZER_SYSTEM, user, openai_key, gemini_key, openai_model, gemini_model)
    new_prompt = str(data.get("new_prompt") or "").strip()
    if not new_prompt or new_prompt == current_prompt.strip():
        raise RuntimeError("optimizer did not propose a new prompt")
    return {
        "diagnosis": str(data.get("diagnosis") or "").strip(),
        "proposed_changes": [str(c).strip() for c in (data.get("proposed_changes") or []) if str(c).strip()][:8],
        "reasoning": str(data.get("reasoning") or "").strip(),
        "expected_impact": str(data.get("expected_impact") or "").strip(),
        "new_prompt": new_prompt,
    }


def _empty_usage() -> dict:
    return {
        "generations": 0,
        "critiques": 0,
        "optimizations": 0,
        "selections": 0,
        "scripts": 0,
        "estimated_model_calls": 0,
    }


def _bump_usage(run: EvolutionRun, key: str, n: int = 1) -> None:
    usage = dict(run.usage or _empty_usage())
    for field in _empty_usage():
        usage.setdefault(field, 0)
    usage[key] = int(usage.get(key) or 0) + n
    usage["estimated_model_calls"] = (
        int(usage["generations"])
        + int(usage["critiques"])
        + int(usage["optimizations"])
        + int(usage["selections"])
        + int(usage["scripts"])
    )
    run.usage = usage


def create_run(
    *,
    questions: list[str],
    max_iterations: int,
    store: PromptLabStore,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
    test_set_id: str = "",
    test_set_name: str = "",
    force_set_id: str = "",
    improvement_threshold: float = DEFAULT_IMPROVEMENT_THRESHOLD,
    stagnant_limit: int = DEFAULT_STAGNANT_ITERS,
) -> EvolutionRun:
    questions = [q.strip() for q in questions if q.strip()]
    if not questions:
        raise ValueError("at least one test question is required")
    max_iterations = clamp_max_iterations(max_iterations)
    active = [r for r in store.list_runs() if r.status in {"queued", "running"}]
    if len(active) >= MAX_CONCURRENT_RUNS:
        raise RuntimeError(f"Already {MAX_CONCURRENT_RUNS} runs in flight. Pause or stop one first.")

    picked, reason, fallback = keyword_select(questions[0], store.list_sets())
    if force_set_id:
        forced = next((s for s in store.list_sets() if s.set_id == force_set_id), None)
        if forced:
            picked, reason, fallback = forced, f"Manually selected {forced.topic}.", forced.is_generic
    if not picked:
        raise RuntimeError("Prompt library is empty.")
    run = EvolutionRun(
        run_id=f"er_{uuid.uuid4().hex[:12]}",
        status="queued",
        questions=questions,
        test_set_id=test_set_id,
        test_set_name=test_set_name,
        prompt_set_id=picked.set_id,
        prompt_topic=picked.topic,
        selected_version=picked.active_version,
        selection_reason=reason,
        fallback=fallback,
        current_version=picked.active_version,
        best_version=picked.active_version,
        max_iterations=max_iterations,
        improvement_threshold=max(0.0, float(improvement_threshold)),
        stagnant_limit=max(1, int(stagnant_limit)),
        usage=_empty_usage(),
    )
    _bump_usage(run, "selections")
    store.save_run(run)
    store.bump_run_count(picked.set_id)
    start_run_worker(
        run.run_id,
        openai_key,
        gemini_key,
        answer_openai_model,
        answer_gemini_model,
        director_openai_model,
        director_gemini_model,
    )
    return run


def start_run_worker(
    run_id: str,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
) -> None:
    with _RUNNING_LOCK:
        if run_id in _RUNNING:
            return
        _RUNNING.add(run_id)
    thread = threading.Thread(
        target=_run_loop,
        args=(
            run_id,
            openai_key,
            gemini_key,
            answer_openai_model,
            answer_gemini_model,
            director_openai_model,
            director_gemini_model,
        ),
        daemon=True,
        name=f"prompt-lab-{run_id}",
    )
    thread.start()


def request_pause(store: PromptLabStore, run_id: str) -> Optional[EvolutionRun]:
    def mut(run: EvolutionRun) -> None:
        if run.status not in {"queued", "running"}:
            return
        run.pause_requested = True
        run.status = "paused"
    return store.mutate_run(run_id, mut)


def request_resume(
    store: PromptLabStore,
    run_id: str,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
) -> Optional[EvolutionRun]:
    def mut(run: EvolutionRun) -> None:
        if run.status not in {"paused", "failed"}:
            return
        run.pause_requested = False
        run.cancel_requested = False
        if run.status == "failed" and run.iteration_count >= run.max_iterations:
            return
        run.status = "queued"
        run.error = ""
    updated = store.mutate_run(run_id, mut)
    if updated and updated.status == "queued":
        start_run_worker(
            run_id,
            openai_key,
            gemini_key,
            answer_openai_model,
            answer_gemini_model,
            director_openai_model,
            director_gemini_model,
        )
    return updated


def request_stop(store: PromptLabStore, run_id: str) -> Optional[EvolutionRun]:
    def mut(run: EvolutionRun) -> None:
        if run.status in {"completed", "max_iterations_reached", "stopped"}:
            return
        run.cancel_requested = True
        run.pause_requested = False
        if run.status in {"queued", "paused"}:
            run.status = "stopped"
            run.stop_reason = "stopped"
            run.finished_at = time.time()
    return store.mutate_run(run_id, mut)


def retry_failed(
    store: PromptLabStore,
    run_id: str,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
) -> Optional[EvolutionRun]:
    def mut(run: EvolutionRun) -> None:
        if run.status != "failed":
            return
        failed = [it for it in run.iterations if it.status == "failed"]
        if failed:
            last = failed[-1]
            if last.retry_count >= ITERATION_RETRY_LIMIT:
                run.iterations = [it for it in run.iterations if not (it.iteration == last.iteration and it.status == "failed")]
                run.iteration_count = max((it.iteration for it in run.iterations if it.status == "complete"), default=0)
            else:
                last.status = "pending"
                last.error = ""
                last.retry_count += 1
                run.iteration_count = max(0, last.iteration - 1)
        run.error = ""
        run.cancel_requested = False
        run.pause_requested = False
        run.status = "queued"
    updated = store.mutate_run(run_id, mut)
    if updated and updated.status == "queued":
        start_run_worker(
            run_id,
            openai_key,
            gemini_key,
            answer_openai_model,
            answer_gemini_model,
            director_openai_model,
            director_gemini_model,
        )
    return updated


def _wait_flags(store: PromptLabStore, run_id: str) -> EvolutionRun:
    while True:
        run = store.get_run(run_id)
        if not run:
            raise RuntimeError("run disappeared")
        if run.cancel_requested:
            return run
        if run.pause_requested or run.status == "paused":
            store.mutate_run(run_id, lambda r: setattr(r, "status", "paused") or setattr(r, "pause_requested", True))
            time.sleep(0.6)
            continue
        return run


def _timed_out(run: EvolutionRun) -> bool:
    if not run.started_at:
        return False
    return (time.time() - run.started_at) > JOB_TIMEOUT_SECONDS


def _run_loop(
    run_id: str,
    openai_key: str,
    gemini_key: str,
    answer_openai_model: str,
    answer_gemini_model: str,
    director_openai_model: str,
    director_gemini_model: str,
) -> None:
    store = PromptLabStore.default()
    try:
        def begin(run: EvolutionRun) -> None:
            if run.status in {"completed", "stopped", "max_iterations_reached"}:
                return
            run.status = "running"
            if not run.started_at:
                run.started_at = time.time()
        store.mutate_run(run_id, begin)
        _ensure_scripts(store, run_id, openai_key, gemini_key, answer_openai_model, answer_gemini_model)
        while True:
            run = _wait_flags(store, run_id)
            if run.cancel_requested:
                store.mutate_run(
                    run_id,
                    lambda r: (
                        setattr(r, "status", "stopped"),
                        setattr(r, "stop_reason", "stopped"),
                        setattr(r, "finished_at", time.time()),
                    ),
                )
                logger.info("run %s stopped", run_id)
                return
            if _timed_out(run):
                store.mutate_run(
                    run_id,
                    lambda r: (
                        setattr(r, "status", "failed"),
                        setattr(r, "error", "job timeout"),
                        setattr(r, "finished_at", time.time()),
                    ),
                )
                return
            if run.status == "paused":
                time.sleep(0.6)
                continue

            completed_ns = {it.iteration for it in run.iterations if it.status == "complete"}
            next_n = 1
            while next_n in completed_ns:
                next_n += 1
            if next_n > run.max_iterations:
                store.mutate_run(
                    run_id,
                    lambda r: (
                        setattr(r, "status", "max_iterations_reached"),
                        setattr(r, "stop_reason", "max_iterations_reached"),
                        setattr(r, "iteration_count", r.max_iterations),
                        setattr(r, "finished_at", time.time()),
                    ),
                )
                return

            pending = next((it for it in run.iterations if it.iteration == next_n and it.status in {"pending", "failed"}), None)
            if pending and pending.status == "complete":
                continue

            _execute_iteration(
                store,
                run_id,
                next_n,
                openai_key,
                gemini_key,
                director_openai_model,
                director_gemini_model,
            )
            run = store.get_run(run_id)
            if not run:
                return
            if run.status in {"completed", "failed", "stopped", "max_iterations_reached"}:
                return
    except Exception as exc:
        logger.exception("prompt lab run %s crashed", run_id)
        store.mutate_run(
            run_id,
            lambda r: (
                setattr(r, "status", "failed"),
                setattr(r, "error", str(exc)[:400]),
                setattr(r, "finished_at", time.time()),
            ),
        )
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(run_id)


def _ensure_scripts(
    store: PromptLabStore,
    run_id: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> None:
    run = store.get_run(run_id)
    if not run:
        return
    missing = [q for q in run.questions if not (run.scripts or {}).get(q)]
    if not missing:
        return
    scripts = dict(run.scripts or {})
    for question in missing:
        text, model = generate_standalone_script(question, openai_key, gemini_key, openai_model, gemini_model)
        if not text:
            raise RuntimeError(f"script agent returned no text for: {question}")
        scripts[question] = text
        logger.info("run %s script ready for %r", run_id, question[:48])

        def save(run: EvolutionRun, q=question, s=text, m=model) -> None:
            run.scripts = {**(run.scripts or {}), q: s}
            run.script_models = {**(run.script_models or {}), q: m}
            _bump_usage(run, "scripts")

        store.mutate_run(run_id, save)


def _execute_iteration(
    store: PromptLabStore,
    run_id: str,
    number: int,
    openai_key: str,
    gemini_key: str,
    director_openai_model: str,
    director_gemini_model: str,
) -> None:
    run = store.get_run(run_id)
    if not run:
        return
    existing = next((it for it in run.iterations if it.iteration == number and it.status == "complete"), None)
    if existing:
        return

    prompt_set = store.get_set(run.prompt_set_id)
    if not prompt_set:
        raise RuntimeError("prompt set missing")
    version = prompt_set.version(run.current_version) or prompt_set.version(prompt_set.active_version)
    if not version:
        raise RuntimeError("prompt version missing")
    prompt_text = version.prompt

    def start(run: EvolutionRun) -> None:
        run.status = "running"
        found = next((it for it in run.iterations if it.iteration == number), None)
        if not found:
            found = EvolutionIteration(
                iteration=number,
                status="generating",
                prompt_set_id=run.prompt_set_id,
                prompt_version=version.version,
                generation_start=time.time(),
                prompt_text=prompt_text,
            )
            run.iterations.append(found)
        else:
            found.status = "generating"
            found.prompt_version = version.version
            found.prompt_text = prompt_text
            found.generation_start = time.time()
            found.error = ""

    store.mutate_run(run_id, start)
    logger.info(
        "run %s iteration %s generate start prompt_version=%s",
        run_id,
        number,
        version.version,
    )

    results: list[QuestionResult] = []
    try:
        for question in run.questions:
            script = (run.scripts or {}).get(question) or ""
            reel = plan_reel(
                question,
                script,
                question[:72],
                openai_key,
                gemini_key,
                director_openai_model,
                director_gemini_model,
                director_system=prompt_text,
                script_model=(run.script_models or {}).get(question) or "",
            )
            if not reel:
                raise RuntimeError(f"director returned no reel for: {question}")
            results.append(QuestionResult(question=question, script=script, reel=reel))
        store.mutate_run(run_id, lambda r: _bump_usage(r, "generations", len(results)))
    except Exception as exc:
        def fail_gen(run: EvolutionRun) -> None:
            it = next(i for i in run.iterations if i.iteration == number)
            it.status = "failed"
            it.error = str(exc)[:400]
            it.generation_end = time.time()
            run.status = "failed"
            run.error = it.error
        store.mutate_run(run_id, fail_gen)
        logger.info("run %s iteration %s generate failed: %s", run_id, number, exc)
        return

    store.mutate_run(
        run_id,
        lambda r: setattr(next(i for i in r.iterations if i.iteration == number), "generation_end", time.time())
        or setattr(next(i for i in r.iterations if i.iteration == number), "critic_start", time.time())
        or setattr(next(i for i in r.iterations if i.iteration == number), "status", "scoring"),
    )

    try:
        for item in results:
            critique = critique_reel(
                item.question,
                item.script,
                item.reel,
                openai_key,
                gemini_key,
                director_openai_model,
                director_gemini_model,
            )
            item.scores = {k: critique[k] for k in SCORE_KEYS}
            item.strengths = critique["strengths"]
            item.weaknesses = critique["weaknesses"]
            item.failure_modes = critique["failure_modes"]
            item.recommendations = critique["recommendations"]
            item.meaningful_remaining_improvement = critique["meaningful_remaining_improvement"]
            item.remaining_improvement_evidence = critique["remaining_improvement_evidence"]
        store.mutate_run(run_id, lambda r: _bump_usage(r, "critiques", len(results)))
    except Exception as exc:
        def fail_crit(run: EvolutionRun) -> None:
            it = next(i for i in run.iterations if i.iteration == number)
            it.status = "failed"
            it.error = f"critic: {exc}"[:400]
            it.critic_end = time.time()
            run.status = "failed"
            run.error = it.error
        store.mutate_run(run_id, fail_crit)
        return

    overall, averages = mean_overall(results)
    prev_best = run.best_score
    accepted = overall >= prev_best
    strengths = []
    weaknesses = []
    failures = []
    recs = []
    for item in results:
        strengths.extend(item.strengths)
        weaknesses.extend(item.weaknesses)
        failures.extend(item.failure_modes)
        recs.extend(item.recommendations)
    critic_stop = all(not item.meaningful_remaining_improvement for item in results)
    evidence = " | ".join(item.remaining_improvement_evidence for item in results if item.remaining_improvement_evidence)

    def score_it(run: EvolutionRun) -> None:
        it = next(i for i in run.iterations if i.iteration == number)
        it.status = "complete"
        it.questions = results
        it.score = overall
        it.scores = averages
        it.strengths = strengths[:10]
        it.weaknesses = weaknesses[:10]
        it.failure_modes = failures[:10]
        it.recommendations = recs[:10]
        it.critic_end = time.time()
        it.delta = round(overall - (prev_best if prev_best >= 0 else overall), 2)
        it.accepted = accepted
        run.iteration_count = max(run.iteration_count, number)
        if accepted:
            run.best_score = overall
            run.best_version = version.version
            run.best_iteration = number
            run.current_version = version.version
            it.is_best = True
            for other in run.iterations:
                if other.iteration != number:
                    other.is_best = False
        else:
            run.current_version = run.best_version
            it.is_best = False
            it.delta = round(overall - (run.best_score if run.best_score >= 0 else overall), 2)
        if accepted:
            improved = (overall - prev_best) if prev_best >= 0 else overall
            if prev_best >= 0 and improved < run.improvement_threshold:
                run.stagnant_count += 1
            else:
                run.stagnant_count = 0
        else:
            run.stagnant_count += 1
        stop, why = should_stop_run(
            iteration_count=run.iteration_count,
            max_iterations=run.max_iterations,
            stagnant_count=run.stagnant_count,
            stagnant_limit=run.stagnant_limit,
            critic_wants_stop=critic_stop,
            critic_evidence=evidence,
            has_failure_modes=bool(failures),
            overall=overall,
        )
        if stop:
            run.stop_reason = why
            run.finished_at = time.time()
            run.status = "max_iterations_reached" if why == "max_iterations_reached" else "completed"

    store.mutate_run(run_id, score_it)
    store.record_set_score(run.prompt_set_id, version.version, overall)
    logger.info(
        "run %s iteration %s score=%.2f accepted=%s best=%s",
        run_id,
        number,
        overall,
        accepted,
        run.best_version,
    )

    run = store.get_run(run_id)
    if not run or run.status in {"completed", "max_iterations_reached", "stopped", "failed"}:
        return
    if run.iteration_count >= run.max_iterations:
        store.mutate_run(
            run_id,
            lambda r: (
                setattr(r, "status", "max_iterations_reached"),
                setattr(r, "stop_reason", "max_iterations_reached"),
                setattr(r, "finished_at", time.time()),
            ),
        )
        return

    baseline = store.get_set(run.prompt_set_id)
    if not baseline:
        return
    best_ver = baseline.version(run.best_version) or version
    history = [
        {
            "iteration": it.iteration,
            "version": it.prompt_version,
            "score": it.score,
            "accepted": it.accepted,
            "failure_modes": it.failure_modes[:4],
        }
        for it in run.iterations
        if it.status == "complete"
    ]
    critique_summary = {
        "overall": overall,
        "scores": averages,
        "failure_modes": failures[:8],
        "recommendations": recs[:8],
        "strengths": strengths[:6],
        "accepted": accepted,
        "best_score": run.best_score,
    }

    def mark_opt_start(run: EvolutionRun) -> None:
        it = next(i for i in run.iterations if i.iteration == number)
        it.optimizer_start = time.time()
        it.status = "optimizing"

    store.mutate_run(run_id, mark_opt_start)
    try:
        proposal = optimize_prompt(
            best_ver.prompt,
            critique_summary,
            history,
            openai_key,
            gemini_key,
            director_openai_model,
            director_gemini_model,
        )
        created = store.add_version(
            run.prompt_set_id,
            proposal["new_prompt"],
            reason=proposal["diagnosis"] or "optimizer",
            parent_version=best_ver.version,
            run_id=run_id,
            test_question=run.questions[0],
            diagnosis=proposal["diagnosis"],
            proposed_changes=proposal["proposed_changes"],
            expected_impact=proposal["expected_impact"],
            llm_model=director_openai_model,
            critic_feedback="; ".join(failures[:4]),
            scores=averages,
            activate=False,
        )
        if not created:
            raise RuntimeError("could not persist new prompt version")

        def finish_opt(run: EvolutionRun) -> None:
            it = next(i for i in run.iterations if i.iteration == number)
            it.status = "complete"
            it.optimizer_end = time.time()
            it.optimizer = {
                "diagnosis": proposal["diagnosis"],
                "proposed_changes": proposal["proposed_changes"],
                "reasoning": proposal.get("reasoning") or "",
                "expected_impact": proposal["expected_impact"],
                "parent_version": best_ver.version,
                "new_version": created.version,
            }
            run.current_version = created.version
            _bump_usage(run, "optimizations")

        store.mutate_run(run_id, finish_opt)
        logger.info("run %s iteration %s optimizer -> v%s", run_id, number, created.version)
    except Exception as exc:
        logger.info("run %s optimizer failed, keeping best v%s: %s", run_id, run.best_version, exc)

        def keep_best(run: EvolutionRun) -> None:
            it = next(i for i in run.iterations if i.iteration == number)
            it.status = "complete"
            it.optimizer_end = time.time()
            it.optimizer = {"error": str(exc)[:300], "kept_best_version": run.best_version}
            run.current_version = run.best_version
            run.status = "completed"
            run.stop_reason = "optimizer_failed_kept_best"
            run.finished_at = time.time()

        store.mutate_run(run_id, keep_best)


def prompt_diff(old: str, new: str) -> list[dict]:
    rows = []
    for line in difflib.ndiff((old or "").splitlines(), (new or "").splitlines()):
        if line.startswith("? "):
            continue
        op = "same"
        if line.startswith("+ "):
            op = "added"
        elif line.startswith("- "):
            op = "removed"
        text = line[2:]
        if not text.strip() and op == "same":
            continue
        rows.append({"op": op, "text": text})
    return rows
