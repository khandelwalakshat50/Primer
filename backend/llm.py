"""One place to pick OpenAI, Gemini, or Claude and to honor the UI choice."""

from __future__ import annotations

import logging
import re
import os
import threading
import time
from contextvars import ContextVar

from openai import OpenAI
from google import genai
from google.genai import types

from metrics import Recorder, timed, usage_from_anthropic, usage_from_gemini, usage_from_openai

logger = logging.getLogger("primer.llm")
PROVIDERS = ("auto", "openai", "gemini", "claude")
_preferred: ContextVar[str | None] = ContextVar("primer_llm", default=None)
_tls = threading.local()


def normalize_provider(raw: str | None) -> str:
    name = (raw or "auto").strip().lower()
    if name in {"anthropic", "sonnet", "opus"}:
        return "claude"
    return name if name in PROVIDERS else "auto"


def prefer(raw: str | None) -> str:
    name = normalize_provider(raw)
    _preferred.set(name)
    _tls.provider = name
    return name


def preferred() -> str:
    name = _preferred.get()
    if name:
        return name
    return getattr(_tls, "provider", None) or "auto"


def _secret(name: str, *placeholders: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value in placeholders:
        return ""
    return value


def openai_key() -> str:
    return _secret("OPENAI_API_KEY", "your_openai_api_key_here")


def gemini_key() -> str:
    return _secret("GEMINI_API_KEY", "your_gemini_api_key_here")


def claude_key() -> str:
    return _secret(
        "ANTHROPIC_API_KEY",
        "your_anthropic_api_key_here",
        "your_claude_api_key_here",
    ) or _secret("CLAUDE_API_KEY", "your_claude_api_key_here", "your_anthropic_api_key_here")


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def director_openai_model() -> str:
    return os.getenv("DIRECTOR_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1"))


def director_gemini_model() -> str:
    return os.getenv("DIRECTOR_GEMINI_MODEL", "gemini-2.5-pro")


def claude_model() -> str:
    return os.getenv("ANTHROPIC_MODEL") or os.getenv("CLAUDE_MODEL") or "claude-opus-5"


def has_any_key() -> bool:
    return bool(openai_key() or gemini_key() or claude_key())


def order(choice: str | None = None) -> list[str]:
    pick = normalize_provider(choice or preferred())
    if pick == "auto":
        return ["openai", "gemini", "claude"]
    # A header pick is a hard choice — do not silently spend a different API.
    return [pick]


# Network blips, rate limits and 5xx are worth waiting out. A course is ~40
# calls; without this, one dropped connection failed a whole lesson.
RETRY_DELAYS = (2.0, 6.0, 15.0)
_TRANSIENT_NAMES = (
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailable",
    "ServerError",
    "ConnectError",
    "ReadTimeout",
    "RemoteProtocolError",
    "OverloadedError",
)
_TRANSIENT_TEXT = re.compile(
    r"\b(429|500|502|503|504|529|overloaded|unavailable|timed? ?out|connection (error|reset|aborted)|rate limit|temporarily)\b",
    re.I,
)


def is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__
    if any(tag in name for tag in _TRANSIENT_NAMES):
        return True
    return bool(_TRANSIENT_TEXT.search(str(exc)[:400]))


def _claude_text(response: object) -> str:
    chunks = []
    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "".join(chunks).strip()


def complete(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    openai_key_value: str | None = None,
    gemini_key_value: str | None = None,
    claude_key_value: str | None = None,
    openai_model_name: str | None = None,
    gemini_model_name: str | None = None,
    claude_model_name: str | None = None,
    recorder: Recorder | None = None,
    step: str = "llm",
    step_label: str = "LLM",
    max_tokens: int = 8192,
    provider: str | None = None,
) -> tuple[str, str]:
    """Try providers in `order()`. Auto may fall back; a header pick may not."""
    pick = normalize_provider(provider or preferred())
    oai = openai_key() if openai_key_value is None else openai_key_value
    gem = gemini_key() if gemini_key_value is None else gemini_key_value
    cla = claude_key() if claude_key_value is None else claude_key_value
    oai_model = openai_model_name or openai_model()
    gem_model = gemini_model_name or gemini_model()
    cla_model = claude_model_name or claude_model()
    system_text = system
    if json_mode and "json" not in system.lower():
        system_text = system + "\n\nReply with JSON only. No markdown."

    last_error = ""

    def attempt_once(name: str) -> tuple[str, str] | None:
        nonlocal last_error
        if name == "openai" and oai:
            client = OpenAI(api_key=oai, timeout=120.0)
            kwargs: dict = {
                "model": oai_model,
                "messages": [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user},
                ],
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            with timed() as clock:
                response = client.chat.completions.create(**kwargs)
            text = (response.choices[0].message.content or "").strip()
            if recorder:
                tin, tout = usage_from_openai(response)
                recorder.add(
                    step,
                    label=step_label,
                    provider="OpenAI",
                    model=oai_model,
                    seconds=clock.seconds,
                    input_tokens=tin,
                    output_tokens=tout,
                    ok=bool(text),
                )
            if text:
                return text, f"OpenAI {oai_model}"
        elif name == "gemini" and gem:
            client = genai.Client(api_key=gem)
            config_kwargs: dict = {"system_instruction": system_text}
            if json_mode:
                config_kwargs["response_mime_type"] = "application/json"
            with timed() as clock:
                response = client.models.generate_content(
                    model=gem_model,
                    contents=user,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
            text = (response.text or "").strip()
            if recorder:
                tin, tout = usage_from_gemini(response)
                recorder.add(
                    step,
                    label=step_label,
                    provider="Gemini",
                    model=gem_model,
                    seconds=clock.seconds,
                    input_tokens=tin,
                    output_tokens=tout,
                    ok=bool(text),
                )
            if text:
                return text, f"Gemini {gem_model}"
        elif name == "claude" and cla:
            from anthropic import Anthropic

            client = Anthropic(api_key=cla, timeout=180.0)
            with timed() as clock:
                response = client.messages.create(
                    model=cla_model,
                    max_tokens=max_tokens,
                    system=system_text,
                    messages=[{"role": "user", "content": user}],
                )
            text = _claude_text(response)
            if recorder:
                tin, tout = usage_from_anthropic(response)
                recorder.add(
                    step,
                    label=step_label,
                    provider="Claude",
                    model=cla_model,
                    seconds=clock.seconds,
                    input_tokens=tin,
                    output_tokens=tout,
                    ok=bool(text),
                )
            if text:
                logger.info("LLM used Claude %s for %s", cla_model, step)
                return text, f"Claude {cla_model}"
            last_error = f"Claude {cla_model} returned an empty response"
        else:
            missing = {
                "openai": not oai,
                "gemini": not gem,
                "claude": not cla,
            }.get(name, True)
            if missing:
                last_error = f"{name} is selected but has no API key"
                logger.warning(last_error)
        return None

    for name in order(pick):
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                got = attempt_once(name)
            except Exception as exc:
                last_error = f"{name} failed: {exc}"
                if attempt < len(RETRY_DELAYS) and is_transient(exc):
                    delay = RETRY_DELAYS[attempt]
                    logger.warning("LLM %s — retrying in %.0fs", last_error, delay)
                    time.sleep(delay)
                    continue
                logger.warning("LLM %s", last_error)
                break
            if got:
                return got
            break
        if pick != "auto" and last_error:
            break
    if pick != "auto":
        raise RuntimeError(last_error or f"{pick} returned an empty response")
    if last_error:
        logger.warning("LLM complete gave up (%s): %s", pick, last_error)
    return "", ""
