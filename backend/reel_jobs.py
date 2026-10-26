"""Durable single-reel generation.

A reel used to be produced inside one long streaming HTTP request. Script,
storyboard and six drawings take minutes, so any dropped connection (a proxy
timeout, a laptop sleeping, uvicorn reloading) threw the whole reel away and
left a "Directing the reel…" stub in the library forever.

Now the request only *creates* a job. The job runs on a server thread, writes
its progress to disk after every step, saves the finished reel into the
library itself, and resumes after a restart from the last finished step.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

import audio_cache
import library
from director import plan_reel
from metrics import Recorder

logger = logging.getLogger("primer.reel_jobs")

JobStatus = Literal["queued", "running", "ready", "failed", "stopped"]
ROOT = Path(__file__).resolve().parent / "data" / "reel_jobs"
MAX_ACTIVE = 2
RESUME_WINDOW_S = 6 * 3600

# The script writer lives in main.py (it shares the notebook prompt). It takes
# the job's request and returns (parsed notebook reply, model label).
ScriptFn = Callable[[dict], tuple[dict, str]]


class ReelJob(BaseModel):
    job_id: str
    created_at: float
    updated_at: float = 0.0
    status: JobStatus = "queued"
    stage: str = "queued"
    stage_label: str = "Queued"
    progress: float = 0.0
    request: dict = Field(default_factory=dict)
    question: str = ""
    topic_title: str = ""
    is_new_topic: bool = False
    handwriting: str = ""
    script_model: str = ""
    reel: Optional[dict] = None
    error: str = ""

    def public(self) -> dict:
        data = self.model_dump(exclude={"request"})
        data["visual_engine"] = self.request.get("visual_engine", "svg")
        return data


class _Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def get(self, job_id: str) -> Optional[ReelJob]:
        if not job_id or "/" in job_id or ".." in job_id:
            return None
        with self._lock:
            path = self._path(job_id)
            if not path.exists():
                return None
            try:
                return ReelJob.model_validate(json.loads(path.read_text()))
            except Exception:
                return None

    def save(self, job: ReelJob) -> None:
        job.updated_at = time.time()
        with self._lock:
            path = self._path(job.job_id)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(job.model_dump(), ensure_ascii=False))
            tmp.replace(path)

    def update(self, job_id: str, **fields) -> Optional[ReelJob]:
        job = self.get(job_id)
        if not job:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        self.save(job)
        return job

    def all(self) -> list[ReelJob]:
        out = []
        for path in self.root.glob("*.json"):
            job = self.get(path.stem)
            if job:
                out.append(job)
        return out


STORE = _Store(ROOT)
_script_fn: Optional[ScriptFn] = None
_running: set[str] = set()
_stop: set[str] = set()
_lock = threading.Lock()


def configure(script_fn: ScriptFn) -> None:
    global _script_fn
    _script_fn = script_fn


def _save_lesson(job: ReelJob) -> None:
    """The library row shares the job id, so the rail can find the job again."""
    if job.status == "ready" and job.reel:
        title = job.reel.get("title") or job.topic_title or "Reel"
    elif job.status == "failed":
        title = "Couldn't finish · retry"
    elif job.status == "stopped":
        title = "Stopped"
    else:
        title = "Generating…"
    try:
        library.upsert_lesson(
            {
                "id": job.job_id,
                "kind": "reel",
                "question": job.question,
                "title": title,
                "handwriting": job.handwriting,
                "reel": job.reel,
                "createdAt": job.created_at,
            }
        )
    except Exception:
        logger.exception("could not save lesson for job %s", job.job_id)


def create(request: dict) -> ReelJob:
    messages = request.get("messages") or []
    question = next(
        (str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    job = ReelJob(
        job_id=uuid.uuid4().hex,
        created_at=time.time(),
        request=request,
        question=question,
        stage_label="Queued",
    )
    STORE.save(job)
    _save_lesson(job)
    _kick(job.job_id)
    return job


def get(job_id: str) -> Optional[ReelJob]:
    job = STORE.get(job_id)
    if job and job.status in {"queued", "running"}:
        # A reload killed the worker: pick it up again on the next poll.
        with _lock:
            alive = job_id in _running
        if not alive:
            _kick(job_id)
    return job


def stop(job_id: str) -> Optional[ReelJob]:
    job = STORE.get(job_id)
    if not job:
        return None
    if job.status in {"queued", "running"}:
        with _lock:
            _stop.add(job_id)
        job = STORE.update(
            job_id, status="stopped", stage="stopped", stage_label="Stopped", error=""
        )
        if job:
            _save_lesson(job)
    return job


def retry(job_id: str) -> Optional[ReelJob]:
    job = STORE.get(job_id)
    if not job:
        return None
    if job.status in {"failed", "stopped"}:
        with _lock:
            _stop.discard(job_id)
        job = STORE.update(
            job_id,
            status="queued",
            stage="queued",
            stage_label="Queued to retry",
            progress=0.15 if job.handwriting else 0.0,
            error="",
        )
        if job:
            _save_lesson(job)
    _kick(job_id)
    return STORE.get(job_id)


def resume_all() -> None:
    cutoff = time.time() - RESUME_WINDOW_S
    for job in sorted(STORE.all(), key=lambda j: j.created_at):
        if job.status in {"queued", "running"} and job.updated_at >= cutoff:
            logger.info("resuming reel job %s after restart", job.job_id)
            _kick(job.job_id)


def _kick(job_id: str) -> None:
    with _lock:
        if job_id in _running or job_id in _stop:
            return
        if len(_running) >= MAX_ACTIVE:
            queued = True
        else:
            queued = False
            _running.add(job_id)
    if queued:
        STORE.update(job_id, stage="queued", stage_label="Waiting for another reel to finish")
        return
    threading.Thread(target=_run, args=(job_id,), daemon=True, name=f"reel-{job_id[:6]}").start()


def _next_waiting() -> None:
    waiting = sorted(
        (j for j in STORE.all() if j.status == "queued"),
        key=lambda j: j.created_at,
    )
    for job in waiting:
        _kick(job.job_id)


def _stopped(job_id: str) -> bool:
    with _lock:
        return job_id in _stop


def _progress(job_id: str, stage: str, label: str, progress: float) -> None:
    if _stopped(job_id):
        return
    STORE.update(
        job_id,
        status="running",
        stage=stage,
        stage_label=label,
        progress=round(max(0.0, min(1.0, progress)), 3),
    )


def _run(job_id: str) -> None:
    import llm

    try:
        job = STORE.get(job_id)
        if not job or job.status not in {"queued", "running"}:
            return
        request = job.request
        llm.prefer(request.get("provider") or "auto")
        recorder = Recorder()

        if not job.handwriting:
            _progress(job_id, "script", "Writing the script", 0.03)
            if _script_fn is None:
                raise RuntimeError("script writer is not configured")
            parsed, label = _script_fn(request)
            if not parsed.get("handwriting"):
                raise RuntimeError("The model returned an empty script.")
            if _stopped(job_id):
                return
            job = STORE.update(
                job_id,
                topic_title=parsed.get("topicTitle") or "",
                is_new_topic=bool(parsed.get("isNewTopic")),
                handwriting=parsed["handwriting"],
                script_model=label,
            )
            _progress(job_id, "storyboard", "Script ready · planning the shots", 0.15)
        if not job:
            return

        def director_progress(stage: str, label: str, fraction: float) -> None:
            _progress(job_id, stage, label, 0.15 + 0.82 * fraction)

        reel = plan_reel(
            job.question,
            job.handwriting,
            job.topic_title,
            llm.openai_key(),
            llm.gemini_key(),
            llm.director_openai_model(),
            llm.director_gemini_model(),
            script_model=job.script_model,
            position="only",
            recorder=recorder,
            visual_engine=request.get("visual_engine") or "svg",
            on_progress=director_progress,
        )
        if _stopped(job_id):
            return
        if not reel:
            raise RuntimeError("The director could not turn the script into a reel.")
        audio_cache.warm_reel_in_background(
            reel,
            os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-athena-en"),
            os.getenv("DEEPGRAM_API_KEY", "").strip(),
        )
        job = STORE.update(
            job_id,
            status="ready",
            stage="ready",
            stage_label="Ready",
            progress=1.0,
            reel=reel,
            error="",
        )
        if job:
            _save_lesson(job)
            import video_export

            video_export.prepare_in_background(reel.get("title") or job.question, [reel])
    except Exception as exc:
        logger.warning("reel job %s failed: %s", job_id, exc)
        if not _stopped(job_id):
            job = STORE.update(
                job_id, status="failed", stage="failed", stage_label="Failed", error=str(exc)[:500]
            )
            if job:
                _save_lesson(job)
    finally:
        with _lock:
            _running.discard(job_id)
            _stop.discard(job_id)
        try:
            _next_waiting()
        except Exception:
            logger.exception("could not start the next reel job")
