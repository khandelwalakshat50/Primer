from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Literal, Optional

from openai import OpenAI
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger("primer.curriculum")

CURRICULUM_SYSTEM = """You are a curriculum architect for a course made of short EXPLAINER VIDEOS — the Vox/3Blue1Brown kind. Before any script gets written, you decide the whole course: how many videos, what each one teaches, and in what order.

Picture your learner: curious, smart, and knows NOTHING about this topic. They are watching on a phone, one short video at a time. They cannot pause to look up a word. If a video assumes something they were never told, they are lost and they leave.

TEACH SLOWLY. ONE IDEA PER REEL.
Each reel carries exactly ONE idea — one thing the learner did not understand before and does after. Not three related ideas, not "an overview of X". If you cannot state a reel's idea in one plain sentence with no "and", it is two reels.
It is always better to go slower with more reels than to cram. A reel that teaches one small thing well beats a reel that mentions five things.

HOW MANY REELS
As many as the topic honestly needs, and no more. Usually 5 to 8. Simple topics may need 4; genuinely layered ones may need 9 or 10.
Never pad to hit a number, and never compress two ideas into one reel to keep the count down. Let the concept dependencies decide the count.

THE SPINE — THIS IS THE COURSE'S PLOT
Name one concrete through-line the whole course walks: one object, one number, one situation that every reel continues. Compounding is "$100 left in a bank for three years". HFT is "the same order sitting in the book". Later reels are the NEXT BEAT of that story, not a new metaphor. If lesson 4 needs a different everyday object than lesson 1, you have lost the wavelength.
Put that through-line in "spine", and make each reel's "example" the next step of THAT same thing (year 2 of the same $100, the same order arriving late — not a fresh analogy).

THE ONE RULE THAT MATTERS
The reels must add up to ONE coherent mental model — by the last reel, someone who watched them in order understands how the topic actually works, as a connected whole, not a pile of facts about it.
Split by CONCEPT DEPENDENCY: what must the learner already understand before the next idea can make sense? Never split by SURFACE FACTS: history, origin stories, fun facts, statistics, "did you know" trivia. A reel earns its place only if it is needed to understand the topic or a later reel.

EVERY REEL NEEDS A CONCRETE EXAMPLE
Explainer video means showing, not defining. For each reel, name in "example" the specific everyday thing the script will walk through — a real object, a number, a person doing something, a before/after. Not "an example of compounding" but "$100 at 10%, year by year, for three years". Abstract reels are the main reason people stop watching.

REEL 1 IS THE HOOK
Reel 1 must be fully graspable with ZERO prior context — every word in it must be a word the learner already owns. It decides whether they keep going, so it lands the single most load-bearing idea of the topic, concretely. Not a history lesson, not a definition dump, not "what we'll cover". depends_on for reel 1 is always empty.

DEPENDENCY IS A CHAIN
This is one YouTube series. Reel n is the next episode after reel n-1, so depends_on for reel 2..N MUST include the immediately previous reel_id (and any earlier reels whose ideas it uses). The viewer hits Next — there cannot be a skipped chapter. A reel may call back to an established idea in one line but may NEVER introduce a term that no listed dependency has taught.

NO OVERLAP, NO FILLER
Two reels must never teach the same concept "for reinforcement". If you see overlap, cut one or merge them. Every reel must be removable-if-and-only-if the course gets worse without it.

DIFFICULTY CURVE
Order from beginner to deeper. "difficulty" is a 0.0-1.0 position on that curve: reel 1 low (0.0-0.15), the last reel highest. It must climb with dependency depth — a reel that depends on reel 3 cannot be easier than reel 3. Make the steps SMALL and even; a jump from 0.2 to 0.8 means you skipped a reel.

BANNED SPLIT PATTERNS (these produce trivia, not a course)
- One reel per fun fact or statistic
- "History of X", "origins of X", "X in pop culture", "X myths"
- "Overview of X" or "X explained" as a whole reel
- Splitting just to hit a count, with no dependency between the reels
- A reel that is only a definition, with nothing shown

GOOD (high-frequency trading — concept-dependency split, one idea each)
1. A price is just the best offer    (zero context; the order book, shown as two queues)
2. Orders arrive in a queue          (depends on 1; first in line gets filled)
3. Being early is worth money        (depends on 1,2; same trade, two arrival times)
4. Market makers earn the gap        (depends on 1,3; buy at 99, sell at 101)
5. A few milliseconds of advantage   (depends on 3,4; fiber cable vs microwave tower)
6. Who pays for the speed race       (depends on 4,5; the slower investor's fill)

BAD (same topic — trivia split, reject this)
1. The history of stock exchanges
2. Famous HFT firms
3. The 2010 flash crash
4. Fun facts about trading speed

OUTPUT
JSON only, no markdown, no commentary:
{
  "topic": "...",
  "spine": "the one concrete object or situation the whole course walks, in one sentence",
  "reels": [
    {
      "reel_id": "reel_01",
      "title": "3-6 word title",
      "objective": "one plain sentence: the single thing the learner understands after this reel",
      "example": "the specific concrete thing this reel walks through",
      "key_terms": ["term one", "term two"],
      "depends_on": [],
      "difficulty": 0.1
    }
  ]
}
key_terms has 2 to 4 entries. depends_on lists only reel_ids that appear earlier in the list.
"""

ReelStatus = Literal["pending", "scripted", "generating", "ready", "failed"]
CurriculumStatus = Literal["planning", "generating", "ready", "failed"]


class ReelSpec(BaseModel):
    reel_id: str
    title: str
    objective: str
    example: str = ""
    key_terms: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    difficulty: float = 0.0
    status: ReelStatus = "pending"
    attempt: int = 0
    error: str = ""
    narration_draft: str = ""
    coherency_notes: list[str] = Field(default_factory=list)
    # Compact record of how this lesson drew its recurring objects, so later
    # lessons reuse the same visual language instead of reinventing it.
    visual_world: str = ""
    # Course bible for this episode: where the object is, how it opens, how it lands.
    outline: str = ""
    # Production telemetry for this lesson (steps, tokens, cost, timing).
    metrics: dict = Field(default_factory=dict)
    script_model: str = ""
    reel: Optional[dict] = None
    # Live progress for the UI: machine stage, what to show the learner, 0..1.
    stage: str = ""
    stage_label: str = ""
    progress: float = 0.0
    started_at: float = 0.0
    updated_at: float = 0.0

    @field_validator("difficulty")
    @classmethod
    def _clamp_difficulty(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @field_validator("key_terms")
    @classmethod
    def _trim_terms(cls, value: list[str]) -> list[str]:
        return [str(v).strip() for v in value if str(v).strip()][:4]


class Curriculum(BaseModel):
    curriculum_id: str
    topic: str
    created_at: float
    reels: list[ReelSpec]
    status: CurriculumStatus = "planning"
    # One object or situation every lesson continues — the course's plot, not a theme.
    spine: str = ""
    provider: str = "auto"
    visual_engine: str = "svg"
    # Learner-pasted document this course teaches. Empty for topic courses.
    source_text: str = ""
    # Why the course is not moving right now (queued behind another course, …).
    note: str = ""

    def reel_by_id(self, reel_id: str) -> ReelSpec | None:
        for reel in self.reels:
            if reel.reel_id == reel_id:
                return reel
        return None

    def dump(self, *, include_source: bool = True) -> dict:
        data = self.model_dump()
        if not include_source:
            data.pop("source_text", None)
        return data


_REEL_ID = re.compile(r"^reel_\d{2}$")


MIN_REELS = 3
MAX_REELS = 10
MIN_SOURCE_REELS = 4
MAX_SOURCE_REELS = 12
SOURCE_CHAR_LIMIT = 48000

SOURCE_PLAN_ADDENDUM = """

SOURCE DOCUMENT MODE
The learner pasted a document. Design the course FROM THAT DOCUMENT, not from a generic topic name.

Cover the important ideas: the load-bearing claims, arguments, mechanisms, numbers, and conclusions. Skip asides, jokes, and trivia. Do not invent chapters that are not in the document. Do not add a history-of or overview reel unless the document itself is teaching that.

Use the document's own object, numbers, and names as the spine when they exist.

HOW MANY REELS HERE
As many as the document honestly needs to be understood by someone who has not read it. Usually 6 to 10. Short notes may need 4 or 5. A dense document may need up to 12. Never pad. Never cram two load-bearing ideas into one reel.

The JSON "topic" field is a short 3-8 word title for the course (not a sentence from the paste).
"""


def clip_source(text: str, limit: int = SOURCE_CHAR_LIMIT) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 24].rstrip() + "\n\n[source truncated]"


def _headline(source: str) -> str:
    for line in (source or "").splitlines():
        piece = line.strip()
        if piece:
            return piece[:80]
    return "Your text"


def _normalize_plan(topic: str, raw: dict, source_text: str = "") -> Curriculum | None:
    source = clip_source(source_text)
    floor = MIN_SOURCE_REELS if source else MIN_REELS
    cap = MAX_SOURCE_REELS if source else MAX_REELS
    reels_in = raw.get("reels")
    if not isinstance(reels_in, list) or not (floor <= len(reels_in) <= cap):
        return None
    seen_ids: list[str] = []
    specs: list[ReelSpec] = []
    for i, item in enumerate(reels_in):
        if not isinstance(item, dict):
            return None
        reel_id = str(item.get("reel_id") or "").strip()
        if not _REEL_ID.match(reel_id) or reel_id in seen_ids:
            reel_id = f"reel_{i + 1:02d}"
        title = str(item.get("title") or "").strip()
        objective = str(item.get("objective") or "").strip()
        example = str(item.get("example") or "").strip()
        if not title or not objective:
            return None
        raw_terms = item.get("key_terms") or []
        if not isinstance(raw_terms, list):
            return None
        terms = [str(t).strip() for t in raw_terms if str(t).strip()]
        if not 2 <= len(terms) <= 4:
            return None
        raw_depends = item.get("depends_on") or []
        depends_on = [str(d).strip() for d in raw_depends if str(d).strip() and str(d).strip() in seen_ids]
        try:
            difficulty = float(item.get("difficulty", i / max(len(reels_in) - 1, 1)))
        except (TypeError, ValueError):
            difficulty = i / max(len(reels_in) - 1, 1)
        seen_ids.append(reel_id)
        specs.append(
            ReelSpec(
                reel_id=reel_id,
                title=title,
                objective=objective,
                example=example,
                key_terms=terms,
                depends_on=depends_on,
                difficulty=difficulty,
            )
        )
    if specs:
        specs[0].depends_on = []
        for i in range(1, len(specs)):
            prev_id = specs[i - 1].reel_id
            extra = [d for d in specs[i].depends_on if d != prev_id]
            specs[i].depends_on = [prev_id, *extra]
    spine = str(raw.get("spine") or "").strip()
    if not spine and specs:
        spine = specs[0].example or specs[0].objective
    planned_title = str(raw.get("topic") or "").strip()
    if source:
        display = planned_title if planned_title and len(planned_title) <= 90 else _headline(source)
    else:
        display = topic.strip() or planned_title
    return Curriculum(
        curriculum_id=uuid.uuid4().hex[:12],
        topic=display.strip() or "Your text",
        created_at=time.time(),
        reels=specs,
        status="planning",
        spine=spine[:240],
        source_text=source,
    )


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


def _openai_models(preferred: str) -> list[str]:
    ordered = [preferred, "gpt-5.6-luna", "gpt-5", "o3", "gpt-4.1", "gpt-4o"]
    seen: list[str] = []
    for model in ordered:
        if model and model not in seen:
            seen.append(model)
    return seen


def _gemini_models(preferred: str) -> list[str]:
    ordered = [preferred, "gemini-2.5-pro", "gemini-2.5-flash"]
    seen: list[str] = []
    for model in ordered:
        if model and model not in seen:
            seen.append(model)
    return seen


def _plan_once(
    topic: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    retry_note: str = "",
    source_text: str = "",
) -> Curriculum | None:
    import llm

    source = clip_source(source_text)
    if source:
        user = (
            "The learner pasted the document below. Design a course that teaches "
            "its important ideas to someone who has not read it.\n\n"
            f"SOURCE DOCUMENT:\n{source}\n\nDesign the syllabus."
        )
        system = CURRICULUM_SYSTEM + SOURCE_PLAN_ADDENDUM
    else:
        user = f"Topic: {topic.strip()}\n\nDesign the syllabus."
        system = CURRICULUM_SYSTEM
        try:
            import web_brief

            user = web_brief.attach(user, web_brief.fetch_brief(topic, openai_key, gemini_key))
        except Exception:
            pass
    if retry_note:
        user += f"\n\n{retry_note}"
    text, _label = llm.complete(
        system,
        user,
        json_mode=True,
        openai_key_value=openai_key,
        gemini_key_value=gemini_key,
        openai_model_name=openai_model,
        gemini_model_name=gemini_model,
    )
    return _normalize_plan(topic, _parse_json(text), source)


OUTLINE_SYSTEM = """You write the episode bible for a course of explainer videos that is ONE YouTube film cut into chapters.

The syllabus is already decided. You do not add or drop lessons. For each reel write:
- object_state: where the spine object is at the START of this episode
- opens_from: one spoken sentence picking up the previous episode (empty for reel 1)
- lands: one spoken sentence this episode ends on — the next episode starts from here

Same object the whole way. Episode n's lands is episode n+1's opens_from. JSON only:
{ "episodes": [ { "reel_id": "reel_01", "object_state": "...", "opens_from": "", "lands": "..." } ] }
"""


def write_episode_outlines(
    curriculum: Curriculum,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
) -> None:
    """One call after the syllabus so parallel scripts still share a plot."""
    import llm

    rows = [
        f"- {r.reel_id}: {r.title} | {r.objective} | example: {r.example}"
        for r in curriculum.reels
    ]
    user = (
        f"Topic: {curriculum.topic}\nSpine: {curriculum.spine}\n\nLessons in order:\n"
        + "\n".join(rows)
        + "\n\nWrite the episode bible."
    )
    text, _label = llm.complete(
        OUTLINE_SYSTEM,
        user,
        json_mode=True,
        openai_key_value=openai_key,
        gemini_key_value=gemini_key,
        openai_model_name=openai_model,
        gemini_model_name=gemini_model,
        step="course_outline",
        step_label="Episode bible",
    )
    data = _parse_json(text)
    by_id: dict[str, dict] = {}
    for item in data.get("episodes") or []:
        if isinstance(item, dict) and item.get("reel_id"):
            by_id[str(item["reel_id"])] = item
    prev_land = ""
    for spec in curriculum.reels:
        item = by_id.get(spec.reel_id) or {}
        state = str(item.get("object_state") or "").strip()
        opens = str(item.get("opens_from") or prev_land).strip()
        lands = str(item.get("lands") or "").strip()
        bits = [p for p in (state and f"Object at start: {state}", opens and f"Opens from: {opens}", lands and f"Lands: {lands}") if p]
        if bits:
            spec.outline = " ".join(bits)
        prev_land = lands
    CurriculumStore.default().save(curriculum)


_TOPIC_GUARD = threading.Lock()
_TOPIC_LOCKS: dict[str, threading.Lock] = {}


def topic_key(topic: str, source_text: str = "") -> str:
    source = (source_text or "").strip()
    if source:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
        return f"src:{digest}"
    return re.sub(r"\s+", " ", (topic or "").strip().lower())


def _item_key(item: Curriculum) -> str:
    return topic_key(item.topic, item.source_text or "")


def _mutex_for_topic(topic: str, source_text: str = "") -> threading.Lock:
    key = topic_key(topic, source_text)
    with _TOPIC_GUARD:
        lock = _TOPIC_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _TOPIC_LOCKS[key] = lock
        return lock


def course_is_open(curriculum: Curriculum) -> bool:
    if curriculum.status in {"planning", "generating"}:
        return True
    return any(r.status not in {"ready", "failed"} for r in curriculum.reels)


def find_open_curriculum(topic: str, source_text: str = "") -> Optional[Curriculum]:
    """An in-flight course for this topic — do not plan a second one."""
    key = topic_key(topic, source_text)
    if not key:
        return None
    matches = [
        item
        for item in CurriculumStore.default().list()
        if _item_key(item) == key and course_is_open(item)
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: item.created_at, reverse=True)
    return matches[0]


def plan_curriculum(
    topic: str,
    openai_key: str,
    gemini_key: str,
    openai_model: str,
    gemini_model: str,
    source_text: str = "",
) -> Curriculum | None:
    source = clip_source(source_text)
    mutex = _mutex_for_topic(topic, source)
    floor = MIN_SOURCE_REELS if source else MIN_REELS
    cap = MAX_SOURCE_REELS if source else MAX_REELS
    usual = "usually 6-10" if source else "usually 5-8"
    with mutex:
        existing = find_open_curriculum(topic, source)
        if existing:
            logger.info("reuse in-flight curriculum %s for topic %s", existing.curriculum_id, topic[:80])
            return existing
        plan = _plan_once(
            topic, openai_key, gemini_key, openai_model, gemini_model, source_text=source
        )
        if not plan:
            plan = _plan_once(
                topic,
                openai_key,
                gemini_key,
                openai_model,
                gemini_model,
                retry_note=(
                    f"Your last syllabus failed validation. Requirements: between {floor} and "
                    f"{cap} reels (pick the number the document or topic needs, {usual}); each with a "
                    "non-empty title, objective, and example; 2 to 4 key_terms; depends_on may only "
                    "reference reel_ids that appear earlier in the list; reel 1 has no dependencies."
                ),
                source_text=source,
            )
        if plan:
            CurriculumStore.default().create(plan)
            try:
                write_episode_outlines(plan, openai_key, gemini_key, openai_model, gemini_model)
            except Exception:
                pass
        return plan


class CurriculumStore:
    """JSON-file-backed store, one file per curriculum. Primer has no database
    yet, so this matches the rest of the app: flat files, no migrations."""

    _instance: "CurriculumStore | None" = None

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @classmethod
    def default(cls) -> "CurriculumStore":
        if cls._instance is None:
            cls._instance = cls(Path(__file__).resolve().parent / "data" / "curricula")
        return cls._instance

    def _path(self, curriculum_id: str) -> Path:
        return self.root / f"{curriculum_id}.json"

    def create(self, curriculum: Curriculum) -> Curriculum:
        self.save(curriculum)
        return curriculum

    def _write_unlocked(self, curriculum: Curriculum) -> None:
        path = self._path(curriculum.curriculum_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(curriculum.dump(), ensure_ascii=False, indent=2))
        tmp.replace(path)

    def _read_unlocked(self, curriculum_id: str) -> Optional[Curriculum]:
        path = self._path(curriculum_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        try:
            return Curriculum.model_validate(data)
        except ValidationError:
            return None

    def save(self, curriculum: Curriculum) -> None:
        with self._lock:
            self._write_unlocked(curriculum)

    def get(self, curriculum_id: str) -> Optional[Curriculum]:
        with self._lock:
            return self._read_unlocked(curriculum_id)

    def update_reel(self, curriculum_id: str, reel_id: str, **fields) -> Optional[Curriculum]:
        """Atomic read-modify-write of ONE reel.

        Reels generate in parallel, so a generator must never persist a whole
        stale snapshot — that would erase a sibling reel that finished while
        this one was running. Only the named fields on the named reel change.
        """
        with self._lock:
            curriculum = self._read_unlocked(curriculum_id)
            if not curriculum:
                return None
            spec = curriculum.reel_by_id(reel_id)
            if not spec:
                return None
            for key, value in fields.items():
                setattr(spec, key, value)
            self._write_unlocked(curriculum)
            return curriculum

    def update_status(self, curriculum_id: str, status: str) -> Optional[Curriculum]:
        with self._lock:
            curriculum = self._read_unlocked(curriculum_id)
            if not curriculum:
                return None
            curriculum.status = status
            self._write_unlocked(curriculum)
            return curriculum

    def fail_unfinished(self, curriculum_id: str, error: str = "Stopped") -> Optional[Curriculum]:
        """Mark every reel that is still in flight as failed so the course can close."""
        with self._lock:
            curriculum = self._read_unlocked(curriculum_id)
            if not curriculum:
                return None
            for spec in curriculum.reels:
                if spec.status not in {"ready", "failed"}:
                    spec.status = "failed"
                    spec.error = error
                    spec.stage = "failed"
                    spec.stage_label = error
                    spec.updated_at = time.time()
            curriculum.note = ""
            curriculum.status = (
                "ready" if any(r.status == "ready" for r in curriculum.reels) else "failed"
            )
            self._write_unlocked(curriculum)
            return curriculum

    def reopen_failed(self, curriculum_id: str) -> Optional[Curriculum]:
        """Put every failed lesson back in the queue, keeping finished scripts."""
        with self._lock:
            curriculum = self._read_unlocked(curriculum_id)
            if not curriculum:
                return None
            for spec in curriculum.reels:
                if spec.status != "failed":
                    continue
                has_script = bool((spec.narration_draft or "").strip())
                spec.status = "scripted" if has_script else "pending"
                spec.error = ""
                spec.attempt = 0
                spec.stage = "queued"
                spec.stage_label = "Queued to retry"
                spec.progress = 0.15 if has_script else 0.0
                spec.updated_at = time.time()
            curriculum.status = "generating"
            curriculum.note = ""
            self._write_unlocked(curriculum)
            return curriculum

    def update_note(self, curriculum_id: str, note: str) -> None:
        with self._lock:
            curriculum = self._read_unlocked(curriculum_id)
            if curriculum and curriculum.note != note:
                curriculum.note = note
                self._write_unlocked(curriculum)

    def list(self) -> list[Curriculum]:
        out: list[Curriculum] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                out.append(Curriculum.model_validate(json.loads(path.read_text())))
            except (json.JSONDecodeError, ValidationError):
                continue
        return out
