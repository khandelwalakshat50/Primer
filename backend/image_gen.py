"""OpenAI stills for mixed-media reels.

Primer overlays type; the image itself must not contain readable text.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import uuid
from pathlib import Path

from openai import OpenAI

from media_safety import SAFETY_RULES, abstract_object_shot, is_moderation_error, scrub_people
from metrics import Recorder, image_cost, timed

logger = logging.getLogger("primer.images")

IMAGE_DIR = Path(__file__).resolve().parent / "data" / "images"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def image_model() -> str:
    return (os.getenv("IMAGE_MODEL") or "gpt-image-1").strip() or "gpt-image-1"


def image_size() -> str:
    return (os.getenv("IMAGE_SIZE") or "1024x1536").strip() or "1024x1536"


def image_path(image_id: str) -> Path:
    if not SAFE_ID.match(image_id):
        raise ValueError("invalid image id")
    png = IMAGE_DIR / f"{image_id}.png"
    if png.is_file():
        return png
    jpg = IMAGE_DIR / f"{image_id}.jpg"
    if jpg.is_file():
        return jpg
    return png


def prompt_for_still(title: str, world_brief: str, shot: str) -> str:
    frame = scrub_people(shot) or "one clear object that stands for the idea"
    world = scrub_people(world_brief) or "(objects only)"
    return (
        "Vertical 9:16 educational still for a phone explainer. Graphic objects "
        "in a simple real space, like a Vox insert frame. Classroom explainer, not a biopic.\n"
        f"Topic label (do not depict people from it): {title.strip() or 'Explainer'}\n"
        f"Shared objects:\n{world}\n"
        f"This frame:\n{frame}\n"
        "STRICT RULES:\n"
        "- Absolutely no text, letters, numbers, captions, titles, subtitles, watermarks, "
        "UI, charts with axes labels, or readable labels anywhere in the image.\n"
        f"{SAFETY_RULES}\n"
        "- Leave clean empty margins at top and bottom so type can be overlaid later.\n"
        "- Portrait composition; subject large and centered or slightly off-center."
    )


def generate_still(
    *,
    title: str,
    world_brief: str,
    shot: str,
    openai_key: str,
    recorder: Recorder | None = None,
) -> dict:
    if not openai_key:
        raise RuntimeError("Add OPENAI_API_KEY to .env to generate stills.")
    model = image_model()
    size = image_size()
    prompts = [
        prompt_for_still(title, world_brief, shot),
        prompt_for_still("", "", abstract_object_shot(shot)),
    ]
    client = OpenAI(api_key=openai_key)
    clock = timed()
    clock.__enter__()
    last_error: Exception | None = None
    image_id = ""
    path = Path()
    ext = "png"
    try:
        for i, prompt in enumerate(prompts):
            try:
                kwargs: dict = {"model": model, "prompt": prompt, "size": size}
                if model.startswith("gpt-image"):
                    kwargs["quality"] = os.getenv("IMAGE_QUALITY") or "medium"
                result = client.images.generate(**kwargs)
                blob, ext = _bytes_from_result(result)
                image_id = uuid.uuid4().hex
                IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                path = IMAGE_DIR / f"{image_id}.{ext}"
                path.write_bytes(blob)
                if path.stat().st_size < 400:
                    path.unlink(missing_ok=True)
                    raise RuntimeError("Image download was empty.")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if i == 0 and is_moderation_error(exc):
                    logger.warning("still blocked the shot; retrying with objects only")
                    continue
                raise
        if last_error:
            raise last_error
    except Exception:
        clock.__exit__()
        if recorder:
            recorder.add(
                "image_render",
                label="OpenAI still",
                provider="OpenAI",
                model=model,
                seconds=clock.seconds,
                ok=False,
                cost_usd=image_cost(model, size),
            )
        raise
    clock.__exit__()
    if recorder:
        recorder.add(
            "image_render",
            label="OpenAI still",
            provider="OpenAI",
            model=model,
            seconds=clock.seconds,
            ok=True,
            cost_usd=image_cost(model, size),
        )
    logger.info("still %s (%s %s) saved to %s", image_id, model, size, path.name)
    mime = "image/jpeg" if ext in {"jpg", "jpeg"} else "image/png"
    return {
        "id": image_id,
        "url": f"/api/images/{image_id}",
        "model": model,
        "size": size,
        "mime": mime,
    }


def _bytes_from_result(result) -> tuple[bytes, str]:
    data = (getattr(result, "data", None) or [None])[0]
    if data is None:
        raise RuntimeError("Image API returned no data.")
    b64 = getattr(data, "b64_json", None)
    if b64:
        return base64.b64decode(b64), "png"
    url = getattr(data, "url", None)
    if url:
        import httpx

        res = httpx.get(url, timeout=60.0)
        if res.status_code >= 400:
            raise RuntimeError(f"Image download failed ({res.status_code})")
        ctype = (res.headers.get("content-type") or "").lower()
        ext = "jpg" if "jpeg" in ctype else "png"
        return res.content, ext
    raise RuntimeError("Image API returned neither b64_json nor url.")
