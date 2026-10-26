"""Live web briefing when the learner asks for news or current events.

Primer's script models have no browse tool of their own. This module searches
first (Gemini Google Search, then OpenAI web_search) and hands the tutor a
short fact sheet so it does not invent headlines.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date

logger = logging.getLogger("primer.web_brief")

_LIVE = re.compile(
    r"\b("
    r"latest|breaking|headline|headlines|news|today|tonight|this week|"
    r"this morning|right now|currently|current events?|happening now|"
    r"just happened|update on|what happened|who won|election|polls?|"
    r"stock|markets?|score"
    r")\b",
    re.I,
)


def needs_live_web(text: str) -> bool:
    blob = (text or "").strip()
    if len(blob) < 8:
        return False
    return bool(_LIVE.search(blob))


def fetch_brief(query: str, openai_key: str = "", gemini_key: str = "") -> str:
    if not needs_live_web(query):
        return ""
    today = date.today().isoformat()
    prompt = (
        f"Today is {today}. Look up current, checkable facts for this learner question:\n"
        f"{query.strip()[:800]}\n\n"
        "Return a short briefing of 8–12 bullets. Each bullet: one fact, a date if known, "
        "and a source name if known. No intro, no markdown headings, no speculation. "
        "If results conflict, say so. If you cannot find it, say that plainly."
    )
    if gemini_key:
        brief = _gemini_search(gemini_key, prompt)
        if brief:
            return brief
    if openai_key:
        brief = _openai_search(openai_key, prompt)
        if brief:
            return brief
    return ""


def attach(user_payload: str, brief: str) -> str:
    if not (brief or "").strip():
        return user_payload
    return (
        user_payload.rstrip()
        + "\n\nLIVE WEB BRIEFING (treat as the facts; do not invent news or dates):\n"
        + brief.strip()[:3500]
    )


def _gemini_search(key: str, prompt: str) -> str:
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )
        text = (getattr(response, "text", None) or "").strip()
        if text:
            logger.info("web brief via Gemini Google Search (%s chars)", len(text))
        return text
    except Exception as exc:
        logger.warning("Gemini web search failed: %s", exc)
        return ""


def _openai_search(key: str, prompt: str) -> str:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        text = (getattr(response, "output_text", None) or "").strip()
        if not text:
            text = _flatten_responses(response)
        if text:
            logger.info("web brief via OpenAI web_search (%s chars)", len(text))
        return text
    except Exception as extra:
        logger.warning("OpenAI web search failed: %s", extra)
        return ""


def _flatten_responses(response) -> str:
    chunks: list[str] = []
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            value = getattr(part, "text", None)
            if value:
                chunks.append(str(value))
    return "\n".join(chunks).strip()
