"""Turn a reel (or a whole course) into an MP4 you can keep or post.

The in-app player is live SVG + streamed narration, so there is no video file
to hand out. This renders one: 1080x1920, H.264 + AAC, the same beats the
player shows — heading, the diagram building group by group, burned-in
captions, and the narration underneath.

Pipeline per export:
  1. voice    every beat's narration comes from the TTS disk cache (or is made now)
  2. frames   each distinct on-screen state is one PNG (resvg, no browser)
  3. encode   each beat becomes a segment (stills + audio, or Sora clip + overlay)
  4. join     segments are concatenated without re-encoding

Exports are keyed by content hash, so asking twice for the same reel is free.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from xml.sax.saxutils import escape

from pydantic import BaseModel

logger = logging.getLogger("primer.export")

VERSION = "v1"
W, H, FPS = 1080, 1920, 30
HOLD_S = 0.5  # breath after narration, same as the player's HOLD_BUFFER_MS
SECONDS_PER_WORD = 0.42  # the player's estimate when there is no audio
CARD_S = 2.2
EXPORT_DIR = Path(__file__).resolve().parent / "data" / "exports"
# Finished videos are the product; keep plenty (~1-2 MB each).
KEEP_EXPORTS = 300

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

SANS = "'Helvetica Neue', Helvetica, Arial, 'Kohinoor Devanagari', 'Devanagari Sangam MN', sans-serif"
INK = "#171717"
STYLE = f"text {{ font-family: {SANS}; }}"

ExportStatus = Literal["queued", "rendering", "encoding", "done", "failed"]


class ExportJob(BaseModel):
    export_id: str
    title: str
    filename: str
    status: ExportStatus = "queued"
    progress: float = 0.0
    stage_label: str = "Queued"
    error: str = ""
    created_at: float = 0.0
    seconds: float = 0.0
    size_bytes: int = 0


_jobs: dict[str, ExportJob] = {}
_jobs_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="export")


# ---------------------------------------------------------------- public API


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise RuntimeError("ffmpeg is not available. Run: pip install imageio-ffmpeg")
        return found


# Fields that do not change a single frame of the video.
_NOT_VISUAL = {"metrics", "script_model", "svg_model", "world"}


def _canonical(value):
    """Same reel, same key — whether it came from Python or a JSON round trip
    through the browser (which turns 1.0 into 1)."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items() if k not in _NOT_VISUAL}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _clean(reels: list[dict]) -> list[dict]:
    return [r for r in reels if isinstance(r, dict) and r.get("hook")]


def export_key(reels: list[dict], lang: str) -> str:
    blob = json.dumps(
        {"v": VERSION, "lang": lang, "reels": _canonical(_clean(reels))},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _meta_path(key: str) -> Path:
    return EXPORT_DIR / f"{key}.json"


def _finished(key: str) -> Optional[ExportJob]:
    out = _out_path(key)
    if not (out.is_file() and out.stat().st_size > 0):
        return None
    meta: dict = {}
    try:
        meta = json.loads(_meta_path(key).read_text())
    except (OSError, json.JSONDecodeError):
        pass
    title = str(meta.get("title") or "Primer reel")
    return ExportJob(
        export_id=key,
        title=title,
        filename=f"{_slug(title) or 'primer-reel'}.mp4",
        status="done",
        progress=1.0,
        stage_label="Ready",
        created_at=float(meta.get("created_at") or out.stat().st_mtime),
        seconds=float(meta.get("seconds") or 0),
        size_bytes=out.stat().st_size,
    )


def lookup(reels: list[dict], lang: str = "en") -> Optional[ExportJob]:
    """The finished or in-progress video for these reels, without starting one."""
    lang = "hi" if lang == "hi" else "en"
    key = export_key(reels, lang)
    with _jobs_lock:
        job = _jobs.get(key)
    if job and job.status != "failed":
        return job
    return _finished(key)


def start(title: str, reels: list[dict], lang: str = "en") -> ExportJob:
    reels = _clean(reels)
    if not reels:
        raise ValueError("nothing to export")
    lang = "hi" if lang == "hi" else "en"
    key = export_key(reels, lang)
    with _jobs_lock:
        job = _jobs.get(key)
        if job and job.status != "failed":
            return job
        done = _finished(key)
        if done:
            _jobs[key] = done
            return done
        job = ExportJob(
            export_id=key,
            title=title,
            filename=f"{_slug(title) or 'primer-reel'}.mp4",
            created_at=time.time(),
        )
        _jobs[key] = job
    _pool.submit(_run, job, reels, lang)
    return job


def prepare_in_background(title: str, reels: list[dict], lang: str = "en") -> None:
    """Build the MP4 as soon as a reel exists, so Download is instant later."""
    try:
        if _clean(reels):
            start(title, reels, lang)
    except Exception:
        logger.exception("could not queue background export")


def get(export_id: str) -> Optional[ExportJob]:
    with _jobs_lock:
        job = _jobs.get(export_id)
    if job:
        return job
    if re.fullmatch(r"[0-9a-f]{24}", export_id or ""):
        # Finished before a restart: still downloadable.
        return _finished(export_id)
    return None


def file_path(export_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[0-9a-f]{24}", export_id or ""):
        return None
    out = _out_path(export_id)
    return out if out.is_file() else None


# ---------------------------------------------------------------- job runner


def _out_path(key: str) -> Path:
    return EXPORT_DIR / f"{key}.mp4"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower())[:60].strip("-")


def _set(job: ExportJob, **fields) -> None:
    with _jobs_lock:
        for k, v in fields.items():
            setattr(job, k, v)


@dataclass
class Beat:
    kind: Literal["svg", "photo", "video", "card"]
    bg: str
    heading: str
    narration: str
    diagram: str = ""
    image_path: Optional[Path] = None
    video_path: Optional[Path] = None
    overlays: list[dict] = field(default_factory=list)
    card_kicker: str = ""
    # filled during voicing
    audio: Optional[Path] = None
    caption_text: str = ""
    speech_s: float = 0.0
    duration: float = 0.0
    # filled by _timeline
    chunks: list[str] = field(default_factory=list)
    # position in the progress strip of its own lesson
    strip_index: int = 0
    strip_total: int = 0


def _run(job: ExportJob, reels: list[dict], lang: str) -> None:
    started = time.time()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="export-", dir=EXPORT_DIR))
    try:
        beats = _beats_for(reels, job.title)
        _set(job, status="rendering", stage_label="Recording narration", progress=0.02)
        _voice(beats, lang, job)

        frames_total = 0
        timelines = []
        for beat in beats:
            states = _timeline(beat)
            timelines.append(states)
            frames_total += len({s.key for s in states})
        done = [0]
        done_lock = threading.Lock()

        def frame_done() -> None:
            with done_lock:
                done[0] += 1
                n = done[0]
            if n % 4 == 0 or n == frames_total:
                _set(
                    job,
                    stage_label=f"Drawing frames · {n} of {frames_total}",
                    progress=0.2 + 0.55 * n / max(1, frames_total),
                )

        _set(job, stage_label=f"Drawing frames · 0 of {frames_total}", progress=0.2)
        segments: list[Path] = []
        seg_jobs = []
        for i, (beat, states) in enumerate(zip(beats, timelines)):
            seg_dir = work / f"b{i:03d}"
            seg_dir.mkdir()
            seg_jobs.append((i, beat, states, seg_dir))

        with ThreadPoolExecutor(max_workers=4) as pool:
            lists = list(pool.map(lambda args: _render_frames(*args, frame_done), seg_jobs))

        _set(job, status="encoding", stage_label="Encoding video", progress=0.76)
        encoded = [0]

        def encode(args) -> Path:
            (i, beat, _states, seg_dir), frame_list = args
            out = seg_dir / "segment.mp4"
            _encode_segment(beat, frame_list, out)
            with done_lock:
                encoded[0] += 1
                n = encoded[0]
            _set(
                job,
                stage_label=f"Encoding video · {n} of {len(beats)} scenes",
                progress=0.76 + 0.2 * n / len(beats),
            )
            return out

        with ThreadPoolExecutor(max_workers=3) as pool:
            segments = list(pool.map(encode, zip(seg_jobs, lists)))

        _set(job, stage_label="Mixing the narration", progress=0.97)
        soundtrack = _soundtrack(beats, work)
        final_tmp = work / "final.mp4"
        concat = work / "segments.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments))
        _ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", str(concat),
                "-i", str(soundtrack),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(final_tmp),
            ]
        )
        out = _out_path(job.export_id)
        final_tmp.replace(out)
        _meta_path(job.export_id).write_text(
            json.dumps(
                {"title": job.title, "created_at": time.time(), "seconds": round(time.time() - started, 1)}
            )
        )
        _set(
            job,
            status="done",
            progress=1.0,
            stage_label="Ready",
            seconds=round(time.time() - started, 1),
            size_bytes=out.stat().st_size,
        )
        logger.info("export %s done in %.1fs (%d beats)", job.export_id, time.time() - started, len(beats))
        _prune()
    except Exception as exc:
        logger.exception("export %s failed", job.export_id)
        _set(job, status="failed", stage_label="Failed", error=str(exc)[:400])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _prune() -> None:
    files = sorted(EXPORT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_EXPORTS:]:
        for path in (old, old.with_suffix(".json")):
            try:
                path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- beats


def _first_text(beat: dict) -> str:
    for item in beat.get("on_screen") or []:
        if item.get("type") in {"text", "stat"} and (item.get("content") or "").strip():
            return str(item["content"]).strip()
    return ""


def _first_svg(beat: dict) -> str:
    for item in beat.get("on_screen") or []:
        if item.get("type") == "svg" and "<svg" in (item.get("content") or ""):
            return str(item["content"])
    return ""


def _media_path(beat: dict, kind: str) -> Optional[Path]:
    try:
        if kind == "video" and beat.get("video"):
            import sora

            path = sora.video_path(str(beat["video"].get("id") or ""))
            return path if path.is_file() else None
        if kind == "image" and beat.get("image"):
            import image_gen

            path = image_gen.image_path(str(beat["image"].get("id") or ""))
            return path if path.is_file() else None
    except ValueError:
        return None
    return None


def _beats_for(reels: list[dict], title: str) -> list[Beat]:
    out: list[Beat] = []
    course = len(reels) > 1
    for n, reel in enumerate(reels):
        raw = [reel.get("hook"), *(reel.get("scenes") or []), reel.get("payoff")]
        raw = [
            b
            for b in raw
            if isinstance(b, dict) and ((b.get("narration") or "").strip() or b.get("on_screen"))
        ]
        if course:
            out.append(
                Beat(
                    kind="card",
                    bg=INK,
                    heading=str(reel.get("title") or f"Lesson {n + 1}"),
                    narration="",
                    card_kicker=(f"{title} · " if n == 0 else "") + f"Lesson {n + 1} of {len(reels)}",
                )
            )
        for i, b in enumerate(raw):
            video = _media_path(b, "video")
            image = _media_path(b, "image")
            kind = "video" if video else "photo" if image else "svg"
            overlays = [
                item
                for item in (b.get("on_screen") or [])
                if item.get("type") in {"text", "stat"} and (item.get("content") or "").strip()
            ]
            out.append(
                Beat(
                    kind=kind,
                    bg=str(b.get("bg") or "#F5F5F5"),
                    heading=_first_text(b),
                    narration=str(b.get("narration") or "").strip(),
                    diagram=_first_svg(b) if kind == "svg" else "",
                    image_path=image,
                    video_path=video,
                    overlays=overlays,
                    strip_index=i,
                    strip_total=len(raw),
                )
            )
    return out


# ---------------------------------------------------------------- voice


def _probe_seconds(path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not match:
        return 0.0
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _voice(beats: list[Beat], lang: str, job: ExportJob) -> None:
    import audio_cache

    model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-athena-en")
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    can_speak = bool(audio_cache.elevenlabs_key() or key)
    work = EXPORT_DIR / "voice"
    work.mkdir(parents=True, exist_ok=True)
    spoken_beats = [b for b in beats if b.narration]
    done = [0]
    lock = threading.Lock()

    def one(beat: Beat) -> None:
        beat.caption_text = beat.narration
        if can_speak:
            try:
                audio, _hit = audio_cache.speak(beat.narration, model, key, lang=lang)
                digest = hashlib.sha256(audio).hexdigest()[:20]
                path = work / f"{digest}.mp3"
                if not path.exists():
                    path.write_bytes(audio)
                beat.audio = path
                beat.speech_s = _probe_seconds(path)
                if lang == "hi":
                    hindi = audio_cache.cached_hindi_text(audio_cache.normalize(beat.narration))
                    if hindi:
                        beat.caption_text = hindi
            except Exception as exc:
                logger.warning("export narration failed, using silence: %s", exc)
                beat.audio = None
        with lock:
            done[0] += 1
            n = done[0]
        _set(
            job,
            stage_label=f"Recording narration · {n} of {len(spoken_beats)}",
            progress=0.02 + 0.16 * n / max(1, len(spoken_beats)),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, spoken_beats))

    for beat in beats:
        if beat.kind == "card":
            beat.duration = CARD_S
            continue
        if not beat.speech_s:
            words = len(beat.narration.split())
            beat.speech_s = words * SECONDS_PER_WORD
        beat.duration = max(1.6, beat.speech_s + HOLD_S)
    # Let the last frame breathe before the video ends.
    if beats:
        beats[-1].duration += 0.8
    # Snap every beat to whole frames so picture and sound cut at the same instant.
    for beat in beats:
        beat.duration = max(1, round(beat.duration * FPS)) / FPS


# ---------------------------------------------------------------- timeline


@dataclass
class State:
    start: float
    groups: int  # fully visible r-el groups
    fading: float  # opacity of the next group (0 = hidden)
    caption: int  # index into caption chunks, -1 = none

    @property
    def key(self) -> tuple:
        return (self.groups, round(self.fading, 2), self.caption)


def _caption_chunks(text: str) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        ends_sentence = word.endswith((".", "!", "?", "।"))
        ends_clause = word.endswith((",", ";", ":", "—"))
        if len(current) >= 8 or ends_sentence or (ends_clause and len(current) >= 4):
            chunks.append(" ".join(current))
            current = []
    if current:
        if chunks and len(current) <= 2:
            chunks[-1] += " " + " ".join(current)
        else:
            chunks.append(" ".join(current))
    return chunks


def _group_count(diagram: str) -> int:
    root = _parse_svg(diagram)
    return len(_reveal_groups(root)) if root is not None else 0


def _timeline(beat: Beat) -> list[State]:
    """Every moment the picture changes, in order."""
    events: dict[float, None] = {0.0: None}
    group_times: list[float] = []
    fade_s = 1 / 15
    if beat.kind == "svg" and beat.diagram:
        n = _group_count(beat.diagram)
        if n:
            window = min(max(0.8, n * 0.22), beat.duration * 0.5)
            group_times = [0.3 + i * window / n for i in range(n)]
            for t in group_times:
                events[round(t, 3)] = None
                events[round(t + fade_s, 3)] = None
    chunks = _caption_chunks(beat.caption_text) if beat.kind != "card" else []
    bounds: list[float] = []
    if chunks:
        total_chars = sum(len(c) for c in chunks)
        speech = max(0.5, beat.speech_s)
        t = 0.0
        for c in chunks:
            bounds.append(t)
            t += speech * len(c) / total_chars
        for b in bounds:
            events[round(b, 3)] = None
    beat.chunks = chunks

    states: list[State] = []
    for t in sorted(e for e in events if e < beat.duration):
        shown = sum(1 for g in group_times if g + fade_s <= t + 1e-6)
        fading = 0.45 if shown < len(group_times) and group_times[shown] <= t + 1e-6 else 0.0
        caption = -1
        for i, b in enumerate(bounds):
            if b <= t + 1e-6:
                caption = i
        state = State(start=t, groups=shown, fading=fading, caption=caption)
        if states and states[-1].key == state.key:
            continue
        states.append(state)
    return states


# ---------------------------------------------------------------- drawing


def _sanitize(raw: str) -> str:
    text = re.sub(r"\sxmlns:ns\d+=(['\"]).*?\1", "", raw)
    text = re.sub(r"</ns\d+:", "</", text)
    text = re.sub(r"<ns\d+:", "<", text)
    text = re.sub(r"\sstroke-dash(?:array|offset)=(['\"]).*?\1", "", text)
    start = text.find("<svg")
    end = text.rfind("</svg>")
    if start < 0 or end < 0:
        return ""
    text = text[start : end + 6]
    if "xmlns=" not in text[: text.find(">")]:
        text = text.replace("<svg", f'<svg xmlns="{SVG_NS}"', 1)
    return text


def _parse_svg(raw: str) -> Optional[ET.Element]:
    text = _sanitize(raw)
    if not text:
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        # Models occasionally emit bare ampersands.
        try:
            return ET.fromstring(re.sub(r"&(?!#?\w+;)", "&amp;", text))
        except ET.ParseError:
            return None


def _reveal_groups(root: ET.Element) -> list[ET.Element]:
    return [
        el
        for el in root.iter()
        if el.tag.endswith("}g") and "r-el" in (el.get("class") or "").split()
    ]


def _diagram_markup(raw: str, state: State, x: float, y: float, w: float, h: float) -> str:
    root = _parse_svg(raw)
    if root is None:
        return ""
    groups = _reveal_groups(root)
    for i, g in enumerate(groups):
        if i < state.groups:
            continue
        g.set("opacity", str(state.fading) if i == state.groups and state.fading else "0")
    if not root.get("viewBox"):
        root.set("viewBox", "0 0 360 440")
    for attr, value in (("x", x), ("y", y), ("width", w), ("height", h)):
        root.set(attr, f"{value:.1f}")
    root.attrib.pop("style", None)
    return ET.tostring(root, encoding="unicode")


def _wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= max_chars or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1 :])]
    return lines


def _text_block(lines: list[str], cx: float, top: float, size: int, weight: int, fill: str, lead: float = 1.12) -> str:
    out = []
    for i, line in enumerate(lines):
        y = top + size * (0.82 + i * lead)
        out.append(
            f'<text x="{cx:.0f}" y="{y:.0f}" text-anchor="middle" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" letter-spacing="-1">{escape(line)}</text>'
        )
    return "".join(out)


def _strip(beat: Beat, light: bool) -> str:
    if beat.strip_total < 2:
        return ""
    gap, left, right, top = 10, 60, W - 60, 56
    width = (right - left - gap * (beat.strip_total - 1)) / beat.strip_total
    on = "#FFFFFF" if light else INK
    parts = []
    for i in range(beat.strip_total):
        x = left + i * (width + gap)
        opacity = 0.9 if i <= beat.strip_index else 0.18
        parts.append(
            f'<rect x="{x:.1f}" y="{top}" width="{width:.1f}" height="6" rx="3" fill="{on}" opacity="{opacity}"/>'
        )
    return "".join(parts)


def _caption(beat: Beat, state: State, light_frame: bool) -> str:
    chunks = beat.chunks
    if state.caption < 0 or state.caption >= len(chunks):
        return ""
    lines = _wrap(chunks[state.caption], 30, 2)
    size, lead = 50, 1.22
    box_h = size * lead * len(lines) + 44
    top = H - 150 - box_h
    box_fill, box_opacity, text_fill = (INK, 0.8, "#FFFFFF") if light_frame else ("#FFFFFF", 0.94, INK)
    widest = max(len(line) for line in lines)
    box_w = min(W - 80, max(360, widest * size * 0.54 + 80))
    return (
        f'<rect x="{(W - box_w) / 2:.0f}" y="{top:.0f}" width="{box_w:.0f}" height="{box_h:.0f}" '
        f'rx="28" fill="{box_fill}" opacity="{box_opacity}"/>'
        + _text_block(lines, W / 2, top + 20, size, 600, text_fill, lead)
    )


def _compose(beat: Beat, state: State, *, transparent: bool = False) -> str:
    parts = [
        f'<svg xmlns="{SVG_NS}" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
    ]
    if beat.kind == "card":
        parts.append(f'<rect width="{W}" height="{H}" fill="{INK}"/>')
        kicker = _wrap(beat.card_kicker.upper(), 34, 2)
        title = _wrap(beat.heading, 18, 4)
        block_h = 40 * 1.3 * len(kicker) + 40 + 96 * 1.1 * len(title)
        top = (H - block_h) / 2
        parts.append(_text_block(kicker, W / 2, top, 40, 600, "#A3A3A3", 1.3))
        parts.append(_text_block(title, W / 2, top + 40 * 1.3 * len(kicker) + 40, 96, 800, "#FFFFFF", 1.1))
        parts.append("</svg>")
        return "".join(parts)

    photo = beat.kind in {"photo", "video"}
    if photo:
        if beat.kind == "photo" and beat.image_path and not transparent:
            mime = "image/jpeg" if beat.image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            data = base64.b64encode(beat.image_path.read_bytes()).decode()
            parts.append(
                f'<image href="data:{mime};base64,{data}" x="0" y="0" width="{W}" height="{H}" '
                'preserveAspectRatio="xMidYMid slice"/>'
            )
        # Legibility wash behind overlay type.
        parts.append(
            '<defs><linearGradient id="wash" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#000" stop-opacity="0.55"/>'
            '<stop offset="0.3" stop-color="#000" stop-opacity="0"/>'
            '<stop offset="0.7" stop-color="#000" stop-opacity="0"/>'
            '<stop offset="1" stop-color="#000" stop-opacity="0.6"/>'
            f'</linearGradient></defs><rect width="{W}" height="{H}" fill="url(#wash)"/>'
        )
        parts.append(_strip(beat, light=True))
        y = 150
        for item in beat.overlays[:3]:
            size = 76 if item.get("emphasis") == "high" else 52
            lines = _wrap(str(item["content"]), 20 if size > 60 else 30, 3)
            parts.append(_text_block(lines, W / 2, y, size, 800 if size > 60 else 600, "#FFFFFF"))
            y += size * 1.12 * len(lines) + 30
        parts.append(_caption(beat, state, light_frame=True))
        parts.append("</svg>")
        return "".join(parts)

    parts.append(f'<rect width="{W}" height="{H}" fill="{beat.bg}"/>')
    parts.append(_strip(beat, light=False))
    heading_lines = _wrap(beat.heading, 20, 3) if beat.heading else []
    size = 78 if len(heading_lines) <= 2 else 66
    heading_top = 130
    parts.append(_text_block(heading_lines, W / 2, heading_top, size, 800, INK, 1.1))
    heading_bottom = heading_top + (size * 1.1 * len(heading_lines) if heading_lines else 0)
    if beat.diagram:
        caption_top = H - 400
        top = heading_bottom + 50
        avail_h = caption_top - top
        dw = min(W - 100, avail_h * 360 / 440)
        dh = dw * 440 / 360
        dx = (W - dw) / 2
        dy = top + max(0.0, (avail_h - dh) / 2)
        parts.append(_diagram_markup(beat.diagram, state, dx, dy, dw, dh))
    parts.append(_caption(beat, state, light_frame=False))
    parts.append("</svg>")
    return "".join(parts)


def _png(svg: str, transparent: bool = False) -> bytes:
    import resvg_py

    return bytes(
        resvg_py.svg_to_bytes(
            svg_string=svg,
            style_sheet=STYLE,
            font_family="Helvetica Neue",
            sans_serif_family="Helvetica Neue",
            background=None if transparent else "#ffffff",
        )
    )


def _render_frames(i: int, beat: Beat, states: list[State], seg_dir: Path, frame_done) -> list[tuple[Path, float]]:
    """PNG per distinct state, plus how long each one stays on screen."""
    transparent = beat.kind == "video"
    cache: dict[tuple, Path] = {}
    timeline: list[tuple[Path, float]] = []
    for n, state in enumerate(states):
        path = cache.get(state.key)
        if path is None:
            path = seg_dir / f"f{len(cache):04d}.png"
            try:
                path.write_bytes(_png(_compose(beat, state, transparent=transparent), transparent))
            except Exception as exc:
                # One malformed diagram should not sink the export: drop it.
                logger.warning("frame render failed (beat %s), drawing without diagram: %s", i, exc)
                beat.diagram = ""
                path.write_bytes(_png(_compose(beat, state, transparent=transparent), transparent))
            cache[state.key] = path
            frame_done()
        end = states[n + 1].start if n + 1 < len(states) else beat.duration
        timeline.append((path, max(1 / FPS, end - state.start)))
    return timeline


# ---------------------------------------------------------------- encoding


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-600:]}")


def _concat_list(frames: list[tuple[Path, float]], path: Path) -> None:
    lines = []
    for frame, seconds in frames:
        lines.append(f"file '{frame.as_posix()}'\nduration {seconds:.4f}\n")
    # The concat demuxer ignores the last duration unless the file repeats.
    lines.append(f"file '{frames[-1][0].as_posix()}'\n")
    path.write_text("".join(lines))


def _encode_segment(beat: Beat, frames: list[tuple[Path, float]], out: Path) -> None:
    """Picture only. Sound is laid down once for the whole film (see _soundtrack)
    so per-scene AAC padding can never push the voice out of step."""
    frames_needed = round(beat.duration * FPS)
    frames_txt = out.with_name("frames.txt")
    _concat_list(frames, frames_txt)
    video_out = [
        "-frames:v", str(frames_needed),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-an",
    ]
    if beat.kind == "video" and beat.video_path:
        _ffmpeg(
            [
                "-stream_loop", "-1", "-i", str(beat.video_path),
                "-f", "concat", "-safe", "0", "-i", str(frames_txt),
                "-filter_complex",
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1[bg];"
                f"[1:v]fps={FPS},format=rgba[ov];[bg][ov]overlay=0:0:shortest=0,format=yuv420p[v]",
                "-map", "[v]", *video_out, str(out),
            ]
        )
        return
    _ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(frames_txt),
            "-vf", f"fps={FPS},scale={W}:{H},setsar=1,format=yuv420p",
            *video_out, str(out),
        ]
    )


def _soundtrack(beats: list[Beat], work: Path) -> Path:
    """One sample-exact PCM track: each beat's voice padded to its exact length."""
    rate = 44100
    parts: list[Path] = []
    for i, beat in enumerate(beats):
        samples = round(beat.duration * rate)
        part = work / f"a{i:03d}.wav"
        source = (
            ["-i", str(beat.audio)]
            if beat.audio
            else ["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=stereo"]
        )
        _ffmpeg(
            [
                *source,
                "-af", f"aresample={rate},apad,atrim=end_sample={samples}",
                "-ar", str(rate), "-ac", "2", "-c:a", "pcm_s16le", str(part),
            ]
        )
        parts.append(part)
    listing = work / "audio.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    track = work / "soundtrack.wav"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(track)])
    return track
