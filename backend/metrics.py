"""Per-step production telemetry: wall time, model, real token counts, cost.

Every LLM call in the reel pipeline reports here, so a finished reel carries
the record of what produced it and what it cost.

Token counts are read from the provider's own usage payload — they are exact,
not estimated. Prices are NOT: they come from the table below, which is only a
set of defaults and will drift as providers change rates. Override them with
the MODEL_PRICES env var (JSON: {"model": {"in": <usd per 1M>, "out": <usd per
1M>}}). A model with no rate reports cost as null rather than a guessed number.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from pydantic import BaseModel, Field

# USD per 1M tokens. Defaults only — verify against current provider pricing.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gpt-4.1": {"in": 2.00, "out": 8.00},
    "gpt-4.1-mini": {"in": 0.40, "out": 1.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gemini-2.5-pro": {"in": 1.25, "out": 10.00},
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50},
    "claude-sonnet-4-5": {"in": 3.00, "out": 15.00},
    "claude-3-5-sonnet": {"in": 3.00, "out": 15.00},
    "claude-3-5-haiku": {"in": 0.80, "out": 4.00},
}


def _load_prices() -> dict[str, dict[str, float]]:
    prices = dict(DEFAULT_PRICES)
    raw = (os.getenv("MODEL_PRICES") or "").strip()
    if raw:
        try:
            extra = json.loads(raw)
            for model, rates in (extra or {}).items():
                if isinstance(rates, dict) and "in" in rates and "out" in rates:
                    prices[str(model)] = {"in": float(rates["in"]), "out": float(rates["out"])}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return prices


def price_for(model: str) -> Optional[dict[str, float]]:
    if not model:
        return None
    prices = _load_prices()
    if model in prices:
        return prices[model]
    # "gpt-4.1-2025-04-14" should match "gpt-4.1"
    for known, rates in sorted(prices.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(known):
            return rates
    return None


def cost_of(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    rates = price_for(model)
    if not rates:
        return None
    return round(
        (input_tokens / 1_000_000) * rates["in"] + (output_tokens / 1_000_000) * rates["out"], 6
    )


# USD per generated second. Defaults from OpenAI's Sora API table (720p sora-2).
DEFAULT_VIDEO_PRICES: dict[str, dict[str, float]] = {
    "sora-2": {"720x1280": 0.10, "1280x720": 0.10},
    "sora-2-pro": {
        "720x1280": 0.30,
        "1280x720": 0.30,
        "1024x1792": 0.50,
        "1792x1024": 0.50,
        "1080x1920": 0.70,
        "1920x1080": 0.70,
    },
}


def video_cost(model: str, seconds: int, size: str) -> Optional[float]:
    if seconds <= 0:
        return None
    rates = DEFAULT_VIDEO_PRICES.get(model) or {}
    if not rates:
        for known, table in sorted(DEFAULT_VIDEO_PRICES.items(), key=lambda kv: -len(kv[0])):
            if model.startswith(known):
                rates = table
                break
    per_second = rates.get(size)
    if per_second is None and rates:
        per_second = next(iter(rates.values()), None)
    if per_second is None:
        return None
    return round(float(seconds) * float(per_second), 6)


# USD per generated still. gpt-image-1 medium quality, OpenAI image pricing table.
DEFAULT_IMAGE_PRICES: dict[str, dict[str, float]] = {
    "gpt-image-1": {"1024x1024": 0.042, "1024x1536": 0.063, "1536x1024": 0.063},
    "dall-e-3": {"1024x1024": 0.040, "1024x1792": 0.080, "1792x1024": 0.080},
}


def image_cost(model: str, size: str) -> Optional[float]:
    rates = DEFAULT_IMAGE_PRICES.get(model) or {}
    if not rates:
        for known, table in sorted(DEFAULT_IMAGE_PRICES.items(), key=lambda kv: -len(kv[0])):
            if model.startswith(known):
                rates = table
                break
    price = rates.get(size)
    if price is None and rates:
        price = next(iter(rates.values()), None)
    if price is None:
        return None
    return round(float(price), 6)


class StepMetric(BaseModel):
    step: str
    label: str = ""
    provider: str = ""
    model: str = ""
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 1
    cost_usd: Optional[float] = None
    ok: bool = True

    def merged(self, other: "StepMetric") -> "StepMetric":
        """Fold a sibling call of the same step (e.g. one SVG render per beat)."""
        total_in = self.input_tokens + other.input_tokens
        total_out = self.output_tokens + other.output_tokens
        costs = [c for c in (self.cost_usd, other.cost_usd) if c is not None]
        return StepMetric(
            step=self.step,
            label=self.label or other.label,
            provider=self.provider or other.provider,
            model=self.model or other.model,
            seconds=round(self.seconds + other.seconds, 3),
            input_tokens=total_in,
            output_tokens=total_out,
            calls=self.calls + other.calls,
            cost_usd=round(sum(costs), 6) if costs else None,
            ok=self.ok and other.ok,
        )


class Recorder(BaseModel):
    """Collects step metrics for ONE reel. Thread-safe: scene renders run in a
    pool, so several steps land concurrently."""

    records: list[StepMetric] = Field(default_factory=list)
    wall_seconds: float = 0.0

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        super().__init__(**data)
        self._lock = threading.Lock()

    def add(
        self,
        step: str,
        *,
        label: str = "",
        provider: str = "",
        model: str = "",
        seconds: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        ok: bool = True,
        cost_usd: Optional[float] = None,
    ) -> None:
        metric = StepMetric(
            step=step,
            label=label or step.replace("_", " ").title(),
            provider=provider,
            model=model,
            seconds=round(seconds, 3),
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            cost_usd=(
                cost_usd
                if cost_usd is not None
                else cost_of(model, int(input_tokens or 0), int(output_tokens or 0))
            ),
            ok=ok,
        )
        with self._lock:
            self.records.append(metric)

    def dump(self) -> list[dict]:
        """One row per step, sibling calls folded together, in pipeline order."""
        with self._lock:
            rows = list(self.records)
        order: list[str] = []
        folded: dict[str, StepMetric] = {}
        for metric in rows:
            if metric.step in folded:
                folded[metric.step] = folded[metric.step].merged(metric)
            else:
                folded[metric.step] = metric
                order.append(metric.step)
        return [folded[step].model_dump() for step in order]


def summarize(steps: list[dict], **extra) -> dict:
    priced = [s["cost_usd"] for s in steps if s.get("cost_usd") is not None]
    out = {
        "steps": steps,
        "llm_seconds": round(sum(s.get("seconds", 0) for s in steps), 3),
        "input_tokens": sum(s.get("input_tokens", 0) for s in steps),
        "output_tokens": sum(s.get("output_tokens", 0) for s in steps),
        "calls": sum(s.get("calls", 0) for s in steps),
        "cost_usd": round(sum(priced), 6) if priced else None,
        "unpriced_models": sorted({s["model"] for s in steps if s.get("cost_usd") is None and s.get("model")}),
    }
    out.update(extra)
    return out


def merge_metrics(first: dict, second: dict) -> dict:
    """Fold two phases of the same lesson (script phase, director phase) into
    one record, keeping each phase's own wall time."""
    steps = list(first.get("steps") or []) + list(second.get("steps") or [])
    seconds = {}
    for key in ("scripted_seconds", "directed_seconds"):
        value = second.get(key) or first.get(key)
        if value:
            seconds[key] = value
    return summarize(steps, **seconds)


def usage_from_openai(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if not usage:
        return 0, 0
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(
        getattr(usage, "completion_tokens", 0) or 0
    )


def usage_from_anthropic(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if not usage:
        return 0, 0
    return int(getattr(usage, "input_tokens", 0) or 0), int(
        getattr(usage, "output_tokens", 0) or 0
    )


def usage_from_gemini(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return 0, 0
    return int(getattr(usage, "prompt_token_count", 0) or 0), int(
        getattr(usage, "candidates_token_count", 0) or 0
    )


class timed:
    """with timed() as t: ...  -> t.seconds"""

    def __enter__(self) -> "timed":
        self._start = time.perf_counter()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.seconds = time.perf_counter() - self._start
