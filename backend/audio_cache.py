"""Narration TTS: ElevenLabs for English (energetic) and Hindi; Deepgram is English fallback."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx

from speech import for_speech

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / "data" / "audio"
DEEPGRAM_URL = "https://api.deepgram.com/v1/speak"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_VOICE = "aura-2-athena-en"
# Lily — warm multilingual narrator for Hindi.
DEFAULT_EL_HI_VOICE = "pFZP5JQG7iQjIQuC4Bku"
# Jessica — bright, playful, expressive American; good explainer energy.
DEFAULT_EL_EN_VOICE = "cgSgspJ2msm6clMCkdW9"
DEFAULT_EL_HI_MODEL = "eleven_multilingual_v2"
DEFAULT_EL_EN_MODEL = "eleven_turbo_v2_5"
EL_PLACEHOLDER = "your_elevenlabs_api_key_here"

EL_EN_VOICE_SETTINGS = {
    "stability": 0.30,
    "similarity_boost": 0.72,
    "style": 0.72,
    "use_speaker_boost": True,
}
EL_HI_VOICE_SETTINGS = {
    "stability": 0.38,
    "similarity_boost": 0.78,
    "style": 0.55,
    "use_speaker_boost": True,
}

HI_TRANSLATE_SYSTEM = (
    "Translate the narration into natural spoken Hindi. "
    "Write Devanagari only. Keep it short, conversational, and easy to hear. "
    "Preserve numbers, proper nouns, and meaning. "
    "No English except unavoidable names. No quotes, labels, or extra lines."
)


# When ElevenLabs refuses the account (no credits, voice needs a paid plan, bad
# key), every English line used to pay a failed round-trip before falling back
# to Deepgram. Remember the refusal for a while instead.
EL_COOLDOWN_S = 6 * 3600
# Remembered on disk: a server reload used to forget it and re-pay the failure.
_EL_BLOCK_FILE = CACHE_DIR / ".elevenlabs-refused"


def _load_block() -> float:
    try:
        return float(_EL_BLOCK_FILE.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


_el_blocked_until = _load_block()
_el_block_reason = ""


def _el_refused(exc: Exception) -> bool:
    return any(f"TTS {code}" in str(exc) for code in (401, 402, 403))


def elevenlabs_english_usable() -> bool:
    return bool(elevenlabs_key()) and time.time() >= _el_blocked_until


def normalize(text: str) -> str:
    return for_speech(text or "")


def cache_key(text: str, model: str) -> str:
    raw = f"{model}\n{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _path(digest: str) -> Path:
    return CACHE_DIR / f"{digest}.mp3"


def cached_path(text: str, model: str) -> Optional[Path]:
    path = _path(cache_key(text, model))
    return path if path.exists() and path.stat().st_size > 0 else None


def peek(text: str, model: str) -> Optional[bytes]:
    """Cached bytes for already-normalized text, or None."""
    path = cached_path(text, model)
    if not path:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None


def elevenlabs_key() -> str:
    value = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not value or value == EL_PLACEHOLDER:
        return ""
    return value


def _el_hi_voice() -> str:
    return os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_EL_HI_VOICE).strip() or DEFAULT_EL_HI_VOICE


def _el_en_voice() -> str:
    return os.getenv("ELEVENLABS_EN_VOICE_ID", DEFAULT_EL_EN_VOICE).strip() or DEFAULT_EL_EN_VOICE


def _el_hi_model() -> str:
    return os.getenv("ELEVENLABS_MODEL", DEFAULT_EL_HI_MODEL).strip() or DEFAULT_EL_HI_MODEL


def _el_en_model() -> str:
    return os.getenv("ELEVENLABS_EN_MODEL", DEFAULT_EL_EN_MODEL).strip() or DEFAULT_EL_EN_MODEL


def _el_cache_model(lang: str) -> str:
    if lang == "hi":
        return f"elevenlabs:{_el_hi_voice()}:{_el_hi_model()}:hi:story"
    return f"elevenlabs:{_el_en_voice()}:{_el_en_model()}:en:bright"


def english_cache_model() -> str:
    return _el_cache_model("en")


def _hi_text_path(english: str) -> Path:
    digest = hashlib.sha256(english.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.hi.txt"


def cached_hindi_text(english: str) -> Optional[str]:
    path = _hi_text_path(english)
    if not path.exists():
        return None
    try:
        spoken = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return spoken or None


def cached_hindi_path(english: str) -> Optional[Path]:
    hindi = cached_hindi_text(english)
    if not hindi:
        return None
    return cached_path(hindi, _el_cache_model("hi"))


def cached_english_path(spoken: str) -> Optional[Path]:
    if elevenlabs_key():
        return cached_path(spoken, _el_cache_model("en"))
    return None


hindi_cached_path = cached_hindi_path


def _translate_hindi(english: str) -> str:
    cached = cached_hindi_text(english)
    if cached:
        return cached
    import llm

    last = ""
    for provider in ("gemini", "openai"):
        try:
            text, _ = llm.complete(
                system=HI_TRANSLATE_SYSTEM,
                user=english,
                provider=provider,
                max_tokens=1024,
                gemini_model_name="gemini-2.5-flash",
                openai_model_name="gpt-4o-mini",
            )
            spoken = (text or "").strip()
            if spoken:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _hi_text_path(english).write_text(spoken, encoding="utf-8")
                return spoken
        except Exception as exc:
            last = str(exc)
    raise RuntimeError(last or "Hindi translation failed.")


def _speak_deepgram(spoken: str, model: str, api_key: str) -> tuple[bytes, bool]:
    hit = peek(spoken, model)
    if hit:
        return hit, True
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    response = httpx.post(
        DEEPGRAM_URL,
        params={"model": model},
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
        json={"text": spoken},
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Deepgram TTS {response.status_code}: {response.text[:400]}")
    audio = response.content
    if not audio:
        raise RuntimeError("Deepgram TTS returned empty audio.")
    _path(cache_key(spoken, model)).write_bytes(audio)
    return audio, False


def _speak_elevenlabs(spoken: str, lang: str) -> tuple[bytes, bool]:
    api_key = elevenlabs_key()
    if not api_key:
        raise RuntimeError("Add ELEVENLABS_API_KEY for spoken audio.")
    language = "hi" if lang == "hi" else "en"
    model_key = _el_cache_model(language)
    hit = peek(spoken, model_key)
    if hit:
        return hit, True
    voice = _el_hi_voice() if language == "hi" else _el_en_voice()
    model_id = _el_hi_model() if language == "hi" else _el_en_model()
    settings = EL_HI_VOICE_SETTINGS if language == "hi" else EL_EN_VOICE_SETTINGS
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "text": spoken,
        "model_id": model_id,
        "language_code": language,
        "voice_settings": settings,
    }
    response = httpx.post(
        f"{ELEVENLABS_URL}/{voice}",
        params={"output_format": "mp3_44100_128"},
        headers={
            "xi-api-key": api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60.0,
    )
    if response.status_code >= 400 and "voice_settings" in payload:
        payload = {k: v for k, v in payload.items() if k != "voice_settings"}
        response = httpx.post(
            f"{ELEVENLABS_URL}/{voice}",
            params={"output_format": "mp3_44100_128"},
            headers={
                "xi-api-key": api_key,
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60.0,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"ElevenLabs TTS {response.status_code}: {response.text[:400]}")
    audio = response.content
    if not audio:
        raise RuntimeError("ElevenLabs TTS returned empty audio.")
    _path(cache_key(spoken, model_key)).write_bytes(audio)
    return audio, False


def speak(
    text: str,
    model: str | None = None,
    api_key: str | None = None,
    lang: str = "en",
) -> tuple[bytes, bool]:
    spoken = normalize(text)
    if not spoken:
        raise ValueError("text required")
    language = (lang or "en").strip().lower()
    if language == "hi":
        hindi = _translate_hindi(spoken)
        return _speak_elevenlabs(hindi, "hi")
    key = (api_key or "").strip()
    voice = (model or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    if elevenlabs_key():
        cached = peek(spoken, _el_cache_model("en"))
        if cached:
            return cached, True
    if key:
        cached = peek(spoken, voice)
        if cached:
            return cached, True
    if elevenlabs_english_usable():
        try:
            return _speak_elevenlabs(spoken, "en")
        except Exception as exc:
            if _el_refused(exc):
                global _el_blocked_until, _el_block_reason
                _el_blocked_until = time.time() + EL_COOLDOWN_S
                _el_block_reason = str(exc)[:200]
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    _EL_BLOCK_FILE.write_text(f"{_el_blocked_until}\n{_el_block_reason}\n")
                except OSError:
                    pass
                logger.warning(
                    "ElevenLabs refused this account; using Deepgram for %d min: %s",
                    EL_COOLDOWN_S // 60,
                    exc,
                )
            else:
                logger.warning("ElevenLabs English failed, trying Deepgram: %s", exc)
    if not key:
        raise RuntimeError("Set ELEVENLABS_API_KEY or DEEPGRAM_API_KEY for English audio.")
    return _speak_deepgram(spoken, voice, key)


def _reel_lines(reel: dict | None) -> list[str]:
    if not reel:
        return []
    parts = [reel.get("hook"), *(reel.get("scenes") or []), reel.get("payoff")]
    lines: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        line = (part.get("narration") or "").strip()
        if line:
            lines.append(line)
    return lines


def warm_reel_in_background(reel: dict | None, model: str, api_key: str) -> None:
    """Prefetch English clips to disk while SVG still renders. Hindi is on-demand."""
    if not reel:
        return
    if not elevenlabs_english_usable() and not (api_key or "").strip():
        return
    lines = _reel_lines(reel)
    if not lines:
        return

    def run() -> None:
        workers = min(3, max(1, len(lines)))

        def _one(line: str) -> None:
            try:
                speak(line, model, api_key, lang="en")
            except Exception:
                logger.debug("TTS warm failed", exc_info=True)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tts-warm") as pool:
            list(pool.map(_one, lines))

    threading.Thread(target=run, daemon=True).start()
