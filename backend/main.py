from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel, Field

import audio_cache
import image_gen
import library
import reel_jobs
import sora
import video_export
import web_brief
from curriculum import CurriculumStore, find_open_curriculum, plan_curriculum
from metrics import Recorder, timed, usage_from_gemini, usage_from_openai
from director import plan_reel
from pipeline import request_stop as request_curriculum_stop
from pipeline import resume_all as resume_all_courses
from pipeline import resume_course, resume_if_needed, start_background_reels
from prompt_lab import (
    create_run,
    prompt_diff,
    request_pause,
    request_resume,
    request_stop,
    retry_failed,
)
from prompt_lab_store import HARD_MAX_ITERATIONS, PromptLabStore, clamp_max_iterations
from speech import for_speech

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    load_dotenv(ROOT / ".env", override=True)
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


_load_env()
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

app = FastAPI(title="Primer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5180", "http://127.0.0.1:5180"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM = """You are Primer, a tutor who writes by hand in a lined paper notebook.

The learner asked because they want to learn this and keep it. Infer the itch behind the question — the mismatch, the "why does that happen", the thing a generic answer would skip — and write so they can store the idea, not so the page looks complete.

Voice and form
- Write as if a fountain pen on paper: warm, clear, slightly personal, a little surprising.
- Plain prose only. No markdown, no bullet symbols, no numbered lists, no headings with hashes, no code fences.
- Short paragraphs separated by a blank line. Keep each reply to what comfortably fits one notebook page (about 90 to 160 words).
- Match the learner. If they are shaky, slow down and use everyday words. If they are sharp, go deeper without showing off.

Make it stick
- After they close the notebook they should be able to retell the idea in two short sentences, with the number or object still in them.
- One through-line: one concrete example with real numbers or a real object, walked through. Lead with that doing something, not with a definition.
- Show cause, not a list of attributes. Prefer a contrast they can feel: expected vs actual, before vs after.
- Teach one idea and let it land. Nearby facts are how generic answers happen — leave them out.
- Ban encyclopedia / chatbot cadence: "X is defined as", "there are several types", "in simple terms", "it's important to note".
- A genuine puzzle is good if it is their itch. Empty hooks are not: no "have you ever wondered", "let's dive in", "imagine a world".

Open and close
- Start with ONE short sentence naming the itch we are about to settle, the way you would say it out loud: "Let's work out why the second year pays more than the first." Then go straight into the example. One sentence — never "in this lesson we will cover".
- End with ONE short sentence they can keep: "So the extra interest is interest on the interest you already earned." Name the conclusion; do not recap the page.
- Both are single sentences inside the same length budget, so keep the middle tighter to make room.

Adaptation
- Infer ability from their questions, mistakes, and how they phrase things.
- Recap only when it helps them hold the idea. Prefer a small check-for-understanding at the end, written as a natural sentence, not a quiz header.

Topics
- topicTitle is a short notebook heading (2 to 5 words).
- isNewTopic is true only when the learner has clearly moved to a different subject, not a follow-up, example, or "explain that again".
- The first reply of a session is not a new topic; write on the current page with isNewTopic false.

You always receive the full conversation and everything already written in the notebook. Use that whole context. Do not repeat the notebook word for word.

If the user message includes a LIVE WEB BRIEFING, those are the current facts. Teach from them. Name the date when it matters. Do not invent headlines, scores, or quotes that are not in the briefing. If the briefing is missing or says the fact was not found, say you could not confirm it live — do not fill the gap from memory.

When a formula or equation would help, explain the idea in words instead of symbols. Do not write LaTeX or stacked equations.

Output format, nothing else:
Line 1: TITLE: <2 to 5 word heading>
Line 2: NEW_TOPIC: yes
or
Line 2: NEW_TOPIC: no
Line 3: blank
Then the notebook handwriting as plain prose.
"""


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Turn]
    notebook: str = ""
    current_topic: str = ""
    visual_engine: str = "svg"
    provider: str = "auto"


class ChatResponse(BaseModel):
    topic_title: str = Field(alias="topicTitle")
    is_new_topic: bool = Field(alias="isNewTopic")
    handwriting: str
    provider: str = "gemini"

    model_config = {"populate_by_name": True}


class SpeakRequest(BaseModel):
    text: str
    lang: str = "en"


class CurriculumRequest(BaseModel):
    topic: str = ""
    source_text: str = ""
    visual_engine: str = "svg"
    provider: str = "auto"


class LessonSave(BaseModel):
    id: str
    kind: str = "reel"
    question: str = ""
    title: str = ""
    handwriting: str = ""
    reel: Optional[dict] = None
    curriculum: Optional[dict] = None
    createdAt: float = 0


class LessonImport(BaseModel):
    lessons: list[dict] = Field(default_factory=list)


def _secret(name: str, placeholder: str = "") -> str:
    value = os.getenv(name, "").strip()
    if not value or value == placeholder:
        return ""
    return value


def _deepgram_key() -> str:
    return _secret("DEEPGRAM_API_KEY", "your_deepgram_api_key_here")


def _elevenlabs_key() -> str:
    return _secret("ELEVENLABS_API_KEY", "your_elevenlabs_api_key_here")


def _gemini_key() -> str:
    return _secret("GEMINI_API_KEY", "your_gemini_api_key_here")


def _openai_key() -> str:
    return _secret("OPENAI_API_KEY", "your_openai_api_key_here")


def _parse_notebook(raw: str, current_topic: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if text.startswith("{"):
        data = _parse_model_json(text)
        return {
            "topicTitle": str(data.get("topicTitle") or current_topic or "Notes").strip(),
            "isNewTopic": bool(data.get("isNewTopic")),
            "handwriting": str(data.get("handwriting") or "").strip(),
        }

    title = current_topic or "Notes"
    is_new = False
    body = text
    title_match = re.search(r"^TITLE:\s*(.+)$", text, re.I | re.M)
    topic_match = re.search(r"^NEW_TOPIC:\s*(yes|no|true|false)\s*$", text, re.I | re.M)
    if title_match:
        title = title_match.group(1).strip()
    if topic_match:
        is_new = topic_match.group(1).lower() in {"yes", "true"}
    split = re.search(r"\n\s*\n", text)
    if split:
        body = text[split.end() :].strip()
    elif topic_match:
        body = text[topic_match.end() :].strip()
    return {"topicTitle": title, "isNewTopic": is_new, "handwriting": body}


def _parse_model_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Model returned non-JSON.") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Model returned invalid JSON.")
    return data


def _visual_engine(raw: str) -> str:
    return "sora" if (raw or "").strip().lower() == "sora" else "svg"


def _require_sora_if_needed(engine: str) -> None:
    if engine == "sora" and not sora.sora_key():
        raise HTTPException(
            status_code=400,
            detail="Sora 2 is on, but no key is set. Add SORA_API_KEY to .env (OpenAI's Sora API key).",
        )


def _payload(req: ChatRequest) -> str:
    history_lines = []
    for turn in req.messages:
        who = "Learner" if turn.role == "user" else "Primer"
        history_lines.append(f"{who}: {turn.content}")
    return (
        f"Current notebook heading: {req.current_topic or '(blank first page)'}\n\n"
        f"Everything already written in the notebook:\n"
        f"{req.notebook.strip() or '(empty)'}\n\n"
        f"Conversation so far:\n" + "\n\n".join(history_lines)
    )


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _question(req: ChatRequest) -> str:
    for turn in reversed(req.messages):
        if turn.role == "user":
            return turn.content
    return ""


def _write_notebook(request: dict) -> tuple[dict, str]:
    """Script step for a reel job: the notebook reply the director will cut."""
    import llm

    _load_env()
    req = ChatRequest.model_validate(request)
    question = _question(req)
    user_payload = web_brief.attach(
        _payload(req),
        web_brief.fetch_brief(question, _openai_key(), _gemini_key()),
    )
    text, label = llm.complete(
        SYSTEM,
        user_payload,
        openai_key_value=_openai_key(),
        gemini_key_value=_gemini_key(),
        claude_key_value=llm.claude_key(),
        claude_model_name=llm.claude_model(),
        step="script",
        step_label="Notebook",
        provider=req.provider,
    )
    return _parse_notebook(text, req.current_topic), label


reel_jobs.configure(_write_notebook)


@app.on_event("startup")
def _resume_unfinished_work() -> None:
    """A reload or crash used to strand every in-flight reel and course."""

    def run() -> None:
        try:
            resume_all_courses()
        except Exception:
            logging.getLogger("primer").exception("could not resume courses")
        try:
            reel_jobs.resume_all()
        except Exception:
            logging.getLogger("primer").exception("could not resume reel jobs")

    threading.Thread(target=run, daemon=True, name="resume").start()


def _emit_reel(question: str, parsed: dict, script_model: str, recorder=None, visual_engine: str = "svg"):
    try:
        reel = plan_reel(
            question,
            parsed["handwriting"],
            parsed["topicTitle"],
            _openai_key(),
            _gemini_key(),
            os.getenv("DIRECTOR_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1")),
            os.getenv("DIRECTOR_GEMINI_MODEL", "gemini-2.5-pro"),
            script_model=script_model,
            position="only",
            recorder=recorder,
            visual_engine=visual_engine,
        )
    except Exception as exc:
        yield _sse({"event": "error", "detail": str(exc)})
        return
    if not reel:
        title = parsed["topicTitle"] or "Takeaway"
        reel = {
            "title": title,
            "hook": {
                "scene_id": "hook",
                "purpose": "hook",
                "narration": parsed["handwriting"][:280],
                "bg": "#EEF3FA",
                "on_screen": [
                    {
                        "type": "text",
                        "content": title.upper()[:24],
                        "emphasis": "high",
                        "reveal": 0,
                        "direction": "",
                    }
                ],
            },
            "scenes": [],
            "payoff": {
                "scene_id": "payoff",
                "purpose": "payoff",
                "narration": "",
                "bg": "#F7EFE6",
                "on_screen": [],
            },
            "script_model": script_model,
        }
    # Start generating narration now, in parallel with the client receiving the
    # reel, so playback is not waiting on a Deepgram round trip.
    audio_cache.warm_reel_in_background(
        reel, os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-athena-en"), _deepgram_key()
    )
    yield _sse({"event": "reel", "reel": reel})


def _stream_openai(user_payload: str, current_topic: str, question: str, visual_engine: str = "svg"):
    key = _openai_key()
    if not key:
        raise RuntimeError("OpenAI API key is not set.")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=key)
    recorder = Recorder()
    clock = timed()
    clock.__enter__()
    stream = client.chat.completions.create(
        model=model,
        stream=True,
        # Streaming hides usage unless it is requested explicitly.
        stream_options={"include_usage": True},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_payload},
        ],
    )
    buf = ""
    usage = None
    for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        delta = ""
        if chunk.choices:
            delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue
        buf += delta
    clock.__exit__()
    recorder.add(
        "script",
        label="Script writing",
        provider="OpenAI",
        model=model,
        seconds=clock.seconds,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        ok=bool(buf),
    )
    parsed = _parse_notebook(buf, current_topic)
    if not parsed["handwriting"]:
        yield _sse({"event": "error", "detail": "Model returned empty handwriting."})
        return
    yield _sse(
        {
            "event": "done",
            "topicTitle": parsed["topicTitle"],
            "isNewTopic": parsed["isNewTopic"],
            "handwriting": parsed["handwriting"],
            "provider": "openai",
        }
    )
    yield from _emit_reel(question, parsed, f"OpenAI {model}", recorder, visual_engine)


def _stream_complete_reply(
    data: dict,
    provider: str,
    current_topic: str,
    question: str,
    script_model: str,
    visual_engine: str = "svg",
):
    parsed = {
        "topicTitle": str(data.get("topicTitle") or current_topic or "Notes").strip(),
        "isNewTopic": bool(data.get("isNewTopic")),
        "handwriting": str(data.get("handwriting") or "").strip(),
    }
    if not parsed["handwriting"]:
        yield _sse({"event": "error", "detail": "Model returned empty handwriting."})
        return
    yield _sse(
        {
            "event": "done",
            "topicTitle": parsed["topicTitle"],
            "isNewTopic": parsed["isNewTopic"],
            "handwriting": parsed["handwriting"],
            "provider": provider,
        }
    )
    yield from _emit_reel(question, parsed, script_model, visual_engine=visual_engine)


def _complete_gemini(user_payload: str) -> dict:
    key = _gemini_key()
    if not key:
        raise RuntimeError("Gemini API key is not set.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=model,
        contents=user_payload,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=0.7,
        ),
    )
    return _parse_notebook(response.text or "", "")


def _complete_openai(user_payload: str) -> dict:
    key = _openai_key()
    if not key:
        raise RuntimeError("OpenAI API key is not set.")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_payload},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    return _parse_notebook(content, "")


@app.get("/api/health")
def health():
    _load_env()
    import llm

    return {
        "ok": True,
        "gemini_model": llm.gemini_model(),
        "openai_model": llm.openai_model(),
        "claude_model": llm.claude_model(),
        "director_model": llm.director_openai_model(),
        "director_gemini_model": llm.director_gemini_model(),
        "has_gemini_key": bool(llm.gemini_key()),
        "has_openai_key": bool(llm.openai_key()),
        "has_claude_key": bool(llm.claude_key()),
        "has_deepgram_key": bool(_deepgram_key()),
        "has_elevenlabs_key": bool(audio_cache.elevenlabs_key()),
        "has_sora_key": bool(sora.sora_key()),
        "sora_model": os.getenv("SORA_MODEL", "sora-2"),
        "deepgram_tts_model": os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-athena-en"),
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages required")

    _load_env()
    engine = _visual_engine(req.visual_engine)
    _require_sora_if_needed(engine)
    question = _question(req)
    user_payload = web_brief.attach(
        _payload(req),
        web_brief.fetch_brief(question, _openai_key(), _gemini_key()),
    )

    def generate():
        import llm

        llm.prefer(req.provider)
        if not llm.has_any_key():
            yield _sse(
                {
                    "event": "error",
                    "detail": "Add OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY to .env.",
                }
            )
            return
        recorder = Recorder()
        try:
            text, label = llm.complete(
                SYSTEM,
                user_payload,
                openai_key_value=_openai_key(),
                gemini_key_value=_gemini_key(),
                claude_key_value=llm.claude_key(),
                claude_model_name=llm.claude_model(),
                recorder=recorder,
                step="script",
                step_label="Notebook",
                provider=req.provider,
            )
        except Exception as exc:
            yield _sse({"event": "error", "detail": str(exc)})
            return
        parsed = _parse_notebook(text, req.current_topic)
        if not parsed["handwriting"]:
            yield _sse({"event": "error", "detail": "Model returned empty handwriting."})
            return
        provider_name = "claude" if label.startswith("Claude") else "openai" if label.startswith("OpenAI") else "gemini"
        yield _sse(
            {
                "event": "done",
                "topicTitle": parsed["topicTitle"],
                "isNewTopic": parsed["isNewTopic"],
                "handwriting": parsed["handwriting"],
                "provider": provider_name,
            }
        )
        yield from _emit_reel(question, parsed, label, recorder, engine)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/reels")
def create_reel_job(req: ChatRequest):
    """Start a reel on the server. Returns at once; poll GET /api/reels/{id}."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages required")
    _load_env()
    import llm

    if not llm.has_any_key():
        raise HTTPException(
            status_code=500,
            detail="Add OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY to .env.",
        )
    engine = _visual_engine(req.visual_engine)
    _require_sora_if_needed(engine)
    request = req.model_dump()
    request["visual_engine"] = engine
    return reel_jobs.create(request).public()


@app.get("/api/reels/{job_id}")
def get_reel_job(job_id: str):
    job = reel_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="reel job not found")
    return job.public()


@app.post("/api/reels/{job_id}/stop")
def stop_reel_job(job_id: str):
    job = reel_jobs.stop(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="reel job not found")
    return job.public()


@app.post("/api/reels/{job_id}/retry")
def retry_reel_job(job_id: str):
    _load_env()
    job = reel_jobs.retry(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="reel job not found")
    return job.public()


@app.post("/api/curriculum")
def create_curriculum(req: CurriculumRequest):
    source = (req.source_text or "").strip()
    topic = (req.topic or "").strip()
    if not topic and not source:
        raise HTTPException(status_code=400, detail="topic required")
    if not topic:
        topic = "Your text"

    _load_env()
    import llm

    engine = _visual_engine(req.visual_engine)
    _require_sora_if_needed(engine)
    llm.prefer(req.provider)
    openai_key = llm.openai_key()
    gemini_key = llm.gemini_key()
    answer_openai_model = llm.openai_model()
    answer_gemini_model = llm.gemini_model()
    director_openai_model = llm.director_openai_model()
    director_gemini_model = llm.director_gemini_model()

    if not llm.has_any_key():
        raise HTTPException(
            status_code=500,
            detail="Add OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY to .env.",
        )

    events: "queue.Queue[dict]" = queue.Queue()
    provider = req.provider

    def plan_and_start() -> None:
        # Runs on its own thread so a dropped request cannot strand a course
        # half-planned: the plan is saved and drawing starts either way.
        llm.prefer(provider)
        try:
            curriculum = plan_curriculum(
                topic,
                openai_key,
                gemini_key,
                director_openai_model,
                director_gemini_model,
                source_text=source,
            )
        except Exception as exc:
            events.put({"event": "error", "detail": str(exc)})
            return
        if not curriculum:
            events.put({"event": "error", "detail": "Could not plan a curriculum for that topic."})
            return
        curriculum.provider = llm.preferred()
        curriculum.visual_engine = engine
        curriculum.status = "generating"
        CurriculumStore.default().save(curriculum)
        events.put({"event": "plan", "curriculum": curriculum.dump(include_source=False)})
        start_background_reels(
            curriculum.curriculum_id,
            openai_key,
            gemini_key,
            answer_openai_model,
            answer_gemini_model,
            director_openai_model,
            director_gemini_model,
            visual_engine=engine,
            provider=llm.preferred(),
        )
        events.put({"event": "background_started"})

    threading.Thread(target=plan_and_start, daemon=True, name="plan-course").start()

    def generate():
        yield _sse({"event": "progress", "stage": "planning", "label": "Designing the syllabus"})
        while True:
            try:
                item = events.get(timeout=4)
            except queue.Empty:
                # Heartbeat: keeps proxies from closing a quiet connection.
                yield _sse({"event": "progress", "stage": "planning", "label": "Designing the syllabus"})
                continue
            yield _sse(item)
            if item.get("event") in {"error", "background_started"}:
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/curriculum/{curriculum_id}")
def get_curriculum(curriculum_id: str, resume: bool = False):
    curriculum = CurriculumStore.default().get(curriculum_id)
    if not curriculum:
        raise HTTPException(status_code=404, detail="curriculum not found")
    if resume:
        _load_env()
        resume_if_needed(curriculum_id)
    return curriculum.dump(include_source=False)


@app.post("/api/curriculum/{curriculum_id}/stop")
def stop_curriculum(curriculum_id: str):
    curriculum = CurriculumStore.default().get(curriculum_id)
    if not curriculum:
        raise HTTPException(status_code=404, detail="curriculum not found")
    stopped = request_curriculum_stop(curriculum_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="curriculum not found")
    return stopped.dump(include_source=False)


@app.post("/api/curriculum/{curriculum_id}/resume")
def resume_curriculum(curriculum_id: str):
    """Retry every failed or stopped lesson; finished ones are kept."""
    _load_env()
    resumed = resume_course(curriculum_id)
    if not resumed:
        raise HTTPException(status_code=404, detail="curriculum not found")
    return resumed.dump(include_source=False)


@app.get("/api/curricula")
def list_curricula():
    items = CurriculumStore.default().list()
    items.sort(key=lambda c: c.created_at, reverse=True)
    return {"curricula": [c.dump(include_source=False) for c in items[:40]]}


@app.post("/api/curricula/stop-open")
def stop_open_curriculum(req: CurriculumRequest):
    found = find_open_curriculum(req.topic, req.source_text or "")
    if not found:
        raise HTTPException(status_code=404, detail="No in-flight course for that topic")
    stopped = request_curriculum_stop(found.curriculum_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="curriculum not found")
    return stopped.dump(include_source=False)


@app.get("/api/lessons")
def get_lessons():
    return {"lessons": library.list_lessons()}


@app.put("/api/lessons")
def put_lesson(req: LessonSave):
    try:
        lessons = library.upsert_lesson(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"lessons": lessons}


@app.delete("/api/lessons/{lesson_id}")
def remove_lesson(lesson_id: str):
    return {"lessons": library.delete_lesson(lesson_id)}


@app.post("/api/lessons/import")
def import_lessons(req: LessonImport):
    return {"lessons": library.import_lessons(req.lessons)}


@app.get("/api/videos/{video_id}")
def get_video(video_id: str):
    try:
        path = sora.video_path(video_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="video not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="video not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/images/{image_id}")
def get_image(image_id: str):
    try:
        path = image_gen.image_path(image_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="image not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(path, media_type=mime)


class ExportRequest(BaseModel):
    title: str = "Primer reel"
    reels: list[dict] = Field(default_factory=list)
    lang: str = "en"


@app.post("/api/exports")
def create_export(req: ExportRequest):
    """Render reels to one MP4 in the background. Poll GET /api/exports/{id}."""
    _load_env()
    try:
        job = video_export.start(req.title.strip() or "Primer reel", req.reels, req.lang)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job.model_dump()


class ExportLookup(BaseModel):
    reels: list[dict] = Field(default_factory=list)
    lang: str = "en"
    title: str = ""
    # Start building it now if it does not exist yet (older lessons made
    # before videos were prepared automatically).
    prepare: bool = False


@app.post("/api/exports/lookup")
def lookup_export(req: ExportLookup):
    """Is there already a video (finished or being made) for these reels?"""
    job = video_export.lookup(req.reels, req.lang)
    if not job and req.prepare:
        _load_env()
        try:
            job = video_export.start(req.title.strip() or "Primer reel", req.reels, req.lang)
        except ValueError:
            job = None
    return job.model_dump() if job else {"status": "none"}


@app.get("/api/exports/{export_id}")
def get_export(export_id: str):
    job = video_export.get(export_id)
    if not job:
        raise HTTPException(status_code=404, detail="export not found")
    return job.model_dump()


@app.get("/api/exports/{export_id}/file")
def download_export(export_id: str, name: str = ""):
    path = video_export.file_path(export_id)
    if not path:
        raise HTTPException(status_code=404, detail="export not ready")
    job = video_export.get(export_id)
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or (job.filename if job else "primer-reel.mp4")
    if not filename.endswith(".mp4"):
        filename += ".mp4"
    return FileResponse(path, media_type="video/mp4", filename=filename)


def _speak_response(text: str, lang: str = "en") -> Response:
    _load_env()
    language = (lang or "en").strip().lower()
    if language not in {"en", "hi"}:
        raise HTTPException(status_code=400, detail="lang must be en or hi.")
    spoken = audio_cache.normalize(text)
    if not spoken:
        raise HTTPException(status_code=400, detail="text required")
    headers = {
        "X-Audio-Cache": "hit",
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-athena-en")
    key = _deepgram_key()
    if language == "hi":
        if not audio_cache.elevenlabs_key():
            raise HTTPException(
                status_code=500,
                detail="Add ELEVENLABS_API_KEY for Hindi audio. Deepgram Aura-2 has no Hindi.",
            )
        path = audio_cache.cached_hindi_path(spoken)
        if path:
            return FileResponse(path, media_type="audio/mpeg", headers=headers)
        try:
            audio, hit = audio_cache.speak(text, model, key, lang="hi")
        except ValueError:
            raise HTTPException(status_code=400, detail="text required")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"ElevenLabs request failed: {exc}") from exc
        headers["X-Audio-Cache"] = "hit" if hit else "miss"
        path = audio_cache.cached_hindi_path(spoken)
        if path:
            return FileResponse(path, media_type="audio/mpeg", headers=headers)
        return Response(content=audio, media_type="audio/mpeg", headers=headers)
    el_path = audio_cache.cached_english_path(spoken)
    if el_path:
        return FileResponse(el_path, media_type="audio/mpeg", headers=headers)
    # Any voice already on disk beats a fresh network call: waiting on a new
    # synthesis is what made narration start late.
    dg_path = audio_cache.cached_path(spoken, model) if key else None
    if dg_path:
        return FileResponse(dg_path, media_type="audio/mpeg", headers=headers)
    if not audio_cache.elevenlabs_english_usable() and not key:
        raise HTTPException(
            status_code=500,
            detail="Set ELEVENLABS_API_KEY or DEEPGRAM_API_KEY for English audio.",
        )
    try:
        audio, hit = audio_cache.speak(text, model, key, lang="en")
    except ValueError:
        raise HTTPException(status_code=400, detail="text required")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS request failed: {exc}") from exc
    headers["X-Audio-Cache"] = "hit" if hit else "miss"
    path = audio_cache.cached_english_path(spoken) or (
        audio_cache.cached_path(spoken, model) if key else None
    )
    if path:
        return FileResponse(path, media_type="audio/mpeg", headers=headers)
    return Response(content=audio, media_type="audio/mpeg", headers=headers)


@app.get("/api/speak")
def speak_get(text: str = "", lang: str = "en"):
    return _speak_response(text, lang)


@app.post("/api/speak")
def speak(req: SpeakRequest):
    return _speak_response(req.text, req.lang)


def _lab_keys():
    _load_env()
    import llm

    openai_key = _openai_key()
    gemini_key = _gemini_key()
    if not openai_key and not gemini_key and not llm.claude_key():
        raise HTTPException(
            status_code=500,
            detail="Add OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY to .env.",
        )
    return {
        "openai_key": openai_key,
        "gemini_key": gemini_key,
        "answer_openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "answer_gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "director_openai_model": os.getenv("DIRECTOR_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1")),
        "director_gemini_model": os.getenv("DIRECTOR_GEMINI_MODEL", "gemini-2.5-pro"),
    }


class PromptSetCreate(BaseModel):
    topic: str
    prompt: str
    description: str = ""
    notes: str = ""
    tags: list[str] = []
    prompt_type: str = "director"


class PromptSetPatch(BaseModel):
    topic: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[list] = None
    prompt_type: Optional[str] = None
    active: Optional[bool] = None


class PromptVersionCreate(BaseModel):
    prompt: str
    reason: str = "manual edit"


class EvolutionCreate(BaseModel):
    question: str = ""
    questions: list[str] = []
    test_set_id: str = ""
    prompt_set_id: str = ""
    max_iterations: int = 5
    improvement_threshold: float = 0.25
    stagnant_limit: int = 2


class PromoteRequest(BaseModel):
    set_id: str
    version: int


class TestSetCreate(BaseModel):
    name: str
    topic: str = ""
    questions: list[str]


@app.get("/api/prompt-lab/overview")
def prompt_lab_overview():
    store = PromptLabStore.default()
    runs = store.list_runs()
    sets = store.list_sets()
    return {
        "hard_max_iterations": HARD_MAX_ITERATIONS,
        "sets": [item.dump() for item in sets],
        "test_sets": [item.dump() for item in store.list_test_sets()],
        "runs": [item.summary() for item in runs],
        "active": [item.summary() for item in runs if item.status in {"queued", "running", "paused"}],
    }


@app.get("/api/prompt-lab/prompts")
def list_prompt_sets():
    return [item.dump() for item in PromptLabStore.default().list_sets()]


@app.post("/api/prompt-lab/prompts")
def create_prompt_set(req: PromptSetCreate):
    try:
        item = PromptLabStore.default().create_set(
            topic=req.topic,
            prompt=req.prompt,
            description=req.description,
            notes=req.notes,
            tags=req.tags,
            prompt_type=req.prompt_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.dump()


@app.patch("/api/prompt-lab/prompts/{set_id}")
def patch_prompt_set(set_id: str, req: PromptSetPatch):
    item = PromptLabStore.default().update_set_meta(set_id, **req.model_dump(exclude_unset=True))
    if not item:
        raise HTTPException(status_code=404, detail="prompt set not found")
    return item.dump()


@app.delete("/api/prompt-lab/prompts/{set_id}")
def delete_prompt_set(set_id: str):
    try:
        ok = PromptLabStore.default().delete_set(set_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="prompt set not found")
    return {"ok": True}


@app.get("/api/prompt-lab/prompts/{set_id}")
def get_prompt_set(set_id: str):
    item = PromptLabStore.default().get_set(set_id)
    if not item:
        raise HTTPException(status_code=404, detail="prompt set not found")
    return item.dump()


@app.post("/api/prompt-lab/prompts/{set_id}/versions")
def add_prompt_version(set_id: str, req: PromptVersionCreate):
    version = PromptLabStore.default().add_version(
        set_id, req.prompt, reason=req.reason or "manual edit", activate=False
    )
    if not version:
        raise HTTPException(status_code=404, detail="prompt set not found")
    return version.model_dump()


@app.post("/api/prompt-lab/prompts/{set_id}/activate/{version}")
def activate_prompt_version(set_id: str, version: int):
    item = PromptLabStore.default().promote_version(set_id, version)
    if not item:
        raise HTTPException(status_code=404, detail="prompt or version not found")
    return item.dump()


@app.get("/api/prompt-lab/test-sets")
def list_test_sets():
    return [item.dump() for item in PromptLabStore.default().list_test_sets()]


@app.post("/api/prompt-lab/test-sets")
def create_test_set(req: TestSetCreate):
    try:
        item = PromptLabStore.default().create_test_set(req.name, req.questions, req.topic)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.dump()


@app.delete("/api/prompt-lab/test-sets/{set_id}")
def delete_test_set(set_id: str):
    if not PromptLabStore.default().delete_test_set(set_id):
        raise HTTPException(status_code=404, detail="test set not found")
    return {"ok": True}


@app.post("/api/prompt-lab/runs")
def start_evolution(req: EvolutionCreate):
    questions = [q.strip() for q in req.questions if q.strip()]
    if req.question.strip():
        questions = [req.question.strip(), *questions]
    test_name = ""
    store = PromptLabStore.default()
    if req.test_set_id:
        found = next((item for item in store.list_test_sets() if item.set_id == req.test_set_id), None)
        if not found:
            raise HTTPException(status_code=404, detail="test set not found")
        questions = list(dict.fromkeys([*questions, *found.questions]))
        test_name = found.name
    questions = list(dict.fromkeys(questions))
    try:
        max_iterations = clamp_max_iterations(req.max_iterations)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    keys = _lab_keys()
    try:
        run = create_run(
            questions=questions,
            max_iterations=max_iterations,
            store=store,
            test_set_id=req.test_set_id,
            test_set_name=test_name,
            force_set_id=req.prompt_set_id.strip(),
            improvement_threshold=req.improvement_threshold,
            stagnant_limit=req.stagnant_limit,
            **keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return run.summary()


@app.get("/api/prompt-lab/runs")
def list_evolution_runs():
    return [item.summary() for item in PromptLabStore.default().list_runs()]


@app.get("/api/prompt-lab/runs/{run_id}")
def get_evolution_run(run_id: str):
    run = PromptLabStore.default().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run.dump()


@app.get("/api/prompt-lab/runs/{run_id}/diff")
def evolution_run_diff(run_id: str, a: int = 0, b: int = 0):
    store = PromptLabStore.default()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    prompt_set = store.get_set(run.prompt_set_id)
    if not prompt_set:
        raise HTTPException(status_code=404, detail="prompt set not found")
    left = prompt_set.version(a or run.selected_version)
    right = prompt_set.version(b or run.best_version)
    if not left or not right:
        raise HTTPException(status_code=404, detail="version not found")
    return {
        "from_version": left.version,
        "to_version": right.version,
        "reason": right.reason,
        "diagnosis": right.diagnosis,
        "expected_impact": right.expected_impact,
        "proposed_changes": right.proposed_changes,
        "lines": prompt_diff(left.prompt, right.prompt),
    }


@app.post("/api/prompt-lab/runs/{run_id}/pause")
def pause_evolution(run_id: str):
    run = request_pause(PromptLabStore.default(), run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run.summary()


@app.post("/api/prompt-lab/runs/{run_id}/resume")
def resume_evolution(run_id: str):
    run = request_resume(PromptLabStore.default(), run_id, **_lab_keys())
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run.summary()


@app.post("/api/prompt-lab/runs/{run_id}/stop")
def stop_evolution(run_id: str):
    run = request_stop(PromptLabStore.default(), run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run.summary()


@app.post("/api/prompt-lab/runs/{run_id}/retry")
def retry_evolution(run_id: str):
    run = retry_failed(PromptLabStore.default(), run_id, **_lab_keys())
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run.summary()


@app.post("/api/prompt-lab/promote")
def promote_prompt(req: PromoteRequest):
    item = PromptLabStore.default().promote_version(req.set_id, req.version)
    if not item:
        raise HTTPException(status_code=404, detail="prompt or version not found")
    return item.dump()
