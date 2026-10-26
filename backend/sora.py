"""Sora 2 video generation for Primer reels.

Mixed-media reels get one portrait clip for a single beat. The clip has no
captions; Primer overlays its own type.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import httpx

from metrics import Recorder, timed, video_cost
from media_safety import SAFETY_RULES, abstract_object_shot, is_moderation_error, scrub_people

logger = logging.getLogger("primer.sora")

API = "https://api.openai.com/v1/videos"
VIDEO_DIR = Path(__file__).resolve().parent / "data" / "videos"
ALLOWED_SECONDS = {"4", "8", "12"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def sora_key() -> str:
    return (os.getenv("SORA_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()


def sora_model() -> str:
    return (os.getenv("SORA_MODEL") or "sora-2").strip() or "sora-2"


def sora_size() -> str:
    return (os.getenv("SORA_SIZE") or "720x1280").strip() or "720x1280"


def clip_seconds(word_count: int) -> str:
    override = (os.getenv("SORA_SECONDS") or "").strip()
    if override in ALLOWED_SECONDS:
        return override
    spoken = word_count * 0.42
    if spoken <= 5:
        return "4"
    if spoken <= 10:
        return "8"
    return "12"


def prompt_for_reel(title: str, world_brief: str, shots: list[str]) -> str:
    shot_block = "\n".join(f"- {line}" for line in shots if line.strip()) or "- one clear visual metaphor for the idea"
    return (
        "Vertical 9:16 educational B-roll for a phone explainer, like a Vox or 3Blue1Brown "
        "insert shot filmed photorealistically with simple graphic objects in the real world.\n"
        f"Lesson title: {title.strip() or 'Explainer'}\n"
        f"Shared visual world:\n{world_brief.strip() or '(none)'}\n"
        "What should happen on camera, in order:\n"
        f"{shot_block}\n"
        "STRICT RULES:\n"
        "- Absolutely no text, letters, numbers, captions, titles, subtitles, watermarks, "
        "UI, or readable labels anywhere in the frame.\n"
        f"{SAFETY_RULES}\n"
        "- Silent visual storytelling only: objects, motion, light, camera.\n"
        "- Keep the same objects and lighting for the whole clip so it can loop under narration."
    )


def prompt_for_beat(title: str, world_brief: str, shot: str) -> str:
    action = scrub_people(shot) or "simple objects transforming on a table"
    world = scrub_people(world_brief) or "(objects only)"
    return (
        "Vertical 9:16 educational B-roll for a phone explainer, like a Vox or 3Blue1Brown "
        "insert shot of graphic objects in a real space. Classroom explainer, not a biopic.\n"
        "This clip is ONE beat of a longer explainer — only this action, not the whole lesson.\n"
        f"Topic label (do not depict people from it): {title.strip() or 'Explainer'}\n"
        f"Shared objects:\n{world}\n"
        f"On camera:\n- {action}\n"
        "STRICT RULES:\n"
        "- Absolutely no text, letters, numbers, captions, titles, subtitles, watermarks, "
        "UI, or readable labels anywhere in the frame.\n"
        f"{SAFETY_RULES}\n"
        "- Silent visual storytelling only: objects, motion, light, camera.\n"
        "- Loop-friendly: start and end in similar compositions."
    )


def video_path(video_id: str) -> Path:
    if not SAFE_ID.match(video_id):
        raise ValueError("invalid video id")
    return VIDEO_DIR / f"{video_id}.mp4"


def generate_reel_video(
    *,
    title: str,
    world_brief: str,
    shots: list[str],
    word_count: int,
    recorder: Recorder | None = None,
) -> dict:
    key = sora_key()
    if not key:
        raise RuntimeError("Add SORA_API_KEY (or OPENAI_API_KEY) to .env to generate Sora videos.")
    model = sora_model()
    size = sora_size()
    seconds = clip_seconds(word_count)
    prompt = prompt_for_reel(title, world_brief, shots)
    clock = timed()
    clock.__enter__()
    try:
        video_id = _create_and_wait(key, prompt, model, size, seconds)
        path = _download(key, video_id)
    except Exception:
        clock.__exit__()
        if recorder:
            recorder.add(
                "sora_render",
                label="Sora video",
                provider="OpenAI",
                model=model,
                seconds=clock.seconds,
                ok=False,
                cost_usd=video_cost(model, int(seconds), size),
            )
        raise
    clock.__exit__()
    if recorder:
        recorder.add(
            "sora_render",
            label="Sora video",
            provider="OpenAI",
            model=model,
            seconds=clock.seconds,
            ok=True,
            cost_usd=video_cost(model, int(seconds), size),
        )
    logger.info("sora clip %s (%ss %s) saved to %s", video_id, seconds, size, path.name)
    return {
        "id": video_id,
        "url": f"/api/videos/{video_id}",
        "model": model,
        "seconds": int(seconds),
        "size": size,
    }


def generate_beat_video(
    *,
    title: str,
    world_brief: str,
    shot: str,
    word_count: int,
    recorder: Recorder | None = None,
) -> dict:
    key = sora_key()
    if not key:
        raise RuntimeError("Add SORA_API_KEY (or OPENAI_API_KEY) to .env to generate Sora videos.")
    model = sora_model()
    size = sora_size()
    seconds = clip_seconds(word_count)
    prompts = [
        prompt_for_beat(title, world_brief, shot),
        prompt_for_beat("", "", abstract_object_shot(shot)),
    ]
    clock = timed()
    clock.__enter__()
    last_error: Exception | None = None
    video_id = ""
    path = Path()
    try:
        for i, prompt in enumerate(prompts):
            try:
                video_id = _create_and_wait(key, prompt, model, size, seconds)
                path = _download(key, video_id)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if i == 0 and is_moderation_error(exc):
                    logger.warning("sora blocked the shot; retrying with objects only")
                    continue
                raise
        if last_error:
            raise last_error
    except Exception:
        clock.__exit__()
        if recorder:
            recorder.add(
                "sora_render",
                label="Sora video",
                provider="OpenAI",
                model=model,
                seconds=clock.seconds,
                ok=False,
                cost_usd=video_cost(model, int(seconds), size),
            )
        raise
    clock.__exit__()
    if recorder:
        recorder.add(
            "sora_render",
            label="Sora video",
            provider="OpenAI",
            model=model,
            seconds=clock.seconds,
            ok=True,
            cost_usd=video_cost(model, int(seconds), size),
        )
    logger.info("sora beat %s (%ss %s) saved to %s", video_id, seconds, size, path.name)
    return {
        "id": video_id,
        "url": f"/api/videos/{video_id}",
        "model": model,
        "seconds": int(seconds),
        "size": size,
    }


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _create_and_wait(key: str, prompt: str, model: str, size: str, seconds: str) -> str:
    timeout = httpx.Timeout(120.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        created = client.post(
            API,
            headers={**_headers(key), "Content-Type": "application/json"},
            json={"model": model, "prompt": prompt, "seconds": seconds, "size": size},
        )
        if created.status_code >= 400:
            raise RuntimeError(_api_error(created, "Sora create failed"))
        payload = created.json()
        video_id = str(payload.get("id") or "")
        if not video_id:
            raise RuntimeError("Sora create returned no video id.")
        deadline = time.time() + 8 * 60
        status = str(payload.get("status") or "queued")
        while status in {"queued", "in_progress", "processing"}:
            if time.time() > deadline:
                raise RuntimeError("Sora video timed out after 8 minutes.")
            time.sleep(8)
            poll = client.get(f"{API}/{video_id}", headers=_headers(key))
            if poll.status_code >= 400:
                raise RuntimeError(_api_error(poll, "Sora poll failed"))
            payload = poll.json()
            status = str(payload.get("status") or "")
        if status != "completed":
            err = payload.get("error") or {}
            detail = err.get("message") if isinstance(err, dict) else status
            raise RuntimeError(f"Sora video {status}: {detail}")
        return video_id


def _download(key: str, video_id: str) -> Path:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    path = video_path(video_id)
    timeout = httpx.Timeout(180.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        res = client.get(f"{API}/{video_id}/content", headers=_headers(key))
        if res.status_code >= 400:
            raise RuntimeError(_api_error(res, "Sora download failed"))
        path.write_bytes(res.content)
    if path.stat().st_size < 1000:
        path.unlink(missing_ok=True)
        raise RuntimeError("Sora download was empty.")
    return path


def _api_error(res: httpx.Response, prefix: str) -> str:
    try:
        data = res.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return f"{prefix}: {err['message']}"
        if isinstance(data, dict) and data.get("detail"):
            return f"{prefix}: {data['detail']}"
    except Exception:
        pass
    return f"{prefix} ({res.status_code})"


def shots_from_board(board) -> tuple[str, list[str], int]:
    world = board.world.brief() if getattr(board, "world", None) else ""
    shots: list[str] = []
    words = 0
    for beat in getattr(board, "beats", []) or []:
        words += len((beat.narration or "").split())
        visual = beat.visual
        piece = " ".join(
            part
            for part in (
                getattr(visual, "sees", ""),
                getattr(visual, "changes", ""),
                getattr(visual, "motion", ""),
            )
            if part
        ).strip()
        if piece:
            shots.append(piece)
    return world, shots, words
