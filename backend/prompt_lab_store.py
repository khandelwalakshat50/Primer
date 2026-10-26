"""JSON-file persistence for the Prompt Evolution Lab.

Matches CurriculumStore: one file per entity, atomic temp+replace writes, a
process lock so background workers cannot clobber each other.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from storyboard import STORYBOARD_SYSTEM

HARD_MAX_ITERATIONS = 10
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_IMPROVEMENT_THRESHOLD = 0.25
DEFAULT_STAGNANT_ITERS = 2
JOB_TIMEOUT_SECONDS = 50 * 60
ITERATION_RETRY_LIMIT = 1
MAX_CONCURRENT_RUNS = 2

SCORE_KEYS = (
    "teaching_quality",
    "coherence",
    "visual_continuity",
    "entertainment",
    "pacing",
    "originality",
    "visual_clarity",
    "narration_quality",
    "phone_readability",
    "overall_score",
)

RUN_STATUSES = (
    "queued",
    "running",
    "paused",
    "completed",
    "failed",
    "stopped",
    "max_iterations_reached",
)


def _now() -> float:
    return time.time()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "topic"


class PromptVersion(BaseModel):
    version_id: str
    version: int
    prompt: str
    parent_version: int = 0
    reason: str = ""
    diagnosis: str = ""
    proposed_changes: list[str] = Field(default_factory=list)
    expected_impact: str = ""
    llm_model: str = ""
    run_id: str = ""
    test_question: str = ""
    critic_feedback: str = ""
    scores: dict = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)


class PromptSet(BaseModel):
    set_id: str
    topic: str
    slug: str
    description: str = ""
    prompt_type: str = "director"
    active: bool = True
    is_generic: bool = False
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    active_version: int = 1
    best_version: int = 1
    best_score: float = 0.0
    run_count: int = 0
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)
    versions: list[PromptVersion] = Field(default_factory=list)

    def version(self, number: int) -> Optional[PromptVersion]:
        for item in self.versions:
            if item.version == number:
                return item
        return None

    def active_prompt(self) -> str:
        item = self.version(self.active_version) or (self.versions[-1] if self.versions else None)
        return item.prompt if item else ""

    def dump(self) -> dict:
        return self.model_dump()


class QuestionResult(BaseModel):
    question: str
    script: str = ""
    reel: dict = Field(default_factory=dict)
    scores: dict = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    meaningful_remaining_improvement: bool = True
    remaining_improvement_evidence: str = ""
    error: str = ""


class EvolutionIteration(BaseModel):
    iteration: int
    status: str = "pending"
    prompt_set_id: str = ""
    prompt_version: int = 1
    accepted: bool = False
    is_best: bool = False
    score: float = 0.0
    delta: float = 0.0
    scores: dict = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    questions: list[QuestionResult] = Field(default_factory=list)
    optimizer: dict = Field(default_factory=dict)
    error: str = ""
    retry_count: int = 0
    prompt_text: str = ""
    generation_start: float = 0.0
    generation_end: float = 0.0
    critic_start: float = 0.0
    critic_end: float = 0.0
    optimizer_start: float = 0.0
    optimizer_end: float = 0.0


class EvolutionRun(BaseModel):
    run_id: str
    status: str = "queued"
    questions: list[str] = Field(default_factory=list)
    test_set_id: str = ""
    test_set_name: str = ""
    prompt_set_id: str = ""
    prompt_topic: str = ""
    selected_version: int = 1
    selection_reason: str = ""
    fallback: bool = False
    current_version: int = 1
    best_version: int = 1
    best_iteration: int = 0
    best_score: float = -1.0
    iteration_count: int = 0
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    improvement_threshold: float = DEFAULT_IMPROVEMENT_THRESHOLD
    stagnant_limit: int = DEFAULT_STAGNANT_ITERS
    stagnant_count: int = 0
    stop_reason: str = ""
    error: str = ""
    scripts: dict[str, str] = Field(default_factory=dict)
    script_models: dict[str, str] = Field(default_factory=dict)
    iterations: list[EvolutionIteration] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel_requested: bool = False
    pause_requested: bool = False

    def dump(self) -> dict:
        return self.model_dump()

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "questions": self.questions,
            "test_set_name": self.test_set_name,
            "prompt_set_id": self.prompt_set_id,
            "prompt_topic": self.prompt_topic,
            "selected_version": self.selected_version,
            "selection_reason": self.selection_reason,
            "fallback": self.fallback,
            "best_version": self.best_version,
            "best_iteration": self.best_iteration,
            "best_score": self.best_score,
            "iteration_count": self.iteration_count,
            "max_iterations": self.max_iterations,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "usage": self.usage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class TestSet(BaseModel):
    set_id: str
    name: str
    topic: str = ""
    questions: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=_now)

    def dump(self) -> dict:
        return self.model_dump()


TOPIC_SEEDS = [
    (
        "Calculus",
        "Limits, derivatives, integrals, instantaneous change.",
        "calculus",
        "TOPIC SPECIALIZATION — CALCULUS\n"
        "The visual world is geometric and kinematic: a curve, a moving secant that becomes a "
        "tangent, a shrinking interval on an axis. A limit is two points sliding together, not "
        "an ε-δ table. Prefer numbers on the curve over a wall of symbols.",
    ),
    (
        "Finance",
        "rates, bonds, compounding, inflation, cash flows, bond prices, interest rates.",
        "finance",
        "TOPIC SPECIALIZATION — FINANCE\n"
        "Treat cash, bonds, and rates as physical objects that transform. A bond is a stack of "
        "promised cash. When rates rise, the old stack looks smaller beside a new one. Do not "
        "draw an abstract supply-demand graph unless the script uses one.",
    ),
    (
        "Economics",
        "Inflation, purchasing power, opportunity cost, trade-offs.",
        "economics",
        "TOPIC SPECIALIZATION — ECONOMICS\n"
        "Show trade-offs as two piles, two paths, or a shrinking basket — never as a labeled "
        "definition. Inflation is the same money buying a visibly smaller pile of goods. "
        "Opportunity cost is the path not taken, still on screen.",
    ),
    (
        "Technology",
        "Neural networks, algorithms, systems that learn.",
        "technology",
        "TOPIC SPECIALIZATION — TECHNOLOGY\n"
        "Machines that learn should look like a process: input, a small adjustable rule, a "
        "wrong guess, a nudge. Prefer one neuron or one weight changing over an architecture "
        "diagram of stacked boxes.",
    ),
    (
        "Physics",
        "Forces, energy, motion, fields.",
        "physics",
        "TOPIC SPECIALIZATION — PHYSICS\n"
        "Show the object that moves and the arrow that causes the motion. Keep the same object "
        "across beats; change only the force, the path, or the energy store.",
    ),
    (
        "Biology",
        "Cells, genes, photosynthesis, living systems.",
        "biology",
        "TOPIC SPECIALIZATION — BIOLOGY\n"
        "Prefer one concrete organism or organelle that the whole reel follows. Photosynthesis "
        "is light hitting a leaf that then holds sugar — not a cycle diagram with eight arrows.",
    ),
]


class PromptLabStore:
    _instance: "PromptLabStore | None" = None

    def __init__(self, root: Path):
        self.root = root
        self.sets_dir = root / "sets"
        self.runs_dir = root / "runs"
        self.sets_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.ensure_seeded()

    @classmethod
    def default(cls) -> "PromptLabStore":
        if cls._instance is None:
            cls._instance = cls(Path(__file__).resolve().parent / "data" / "prompt_lab")
        return cls._instance

    def _set_path(self, set_id: str) -> Path:
        return self.sets_dir / f"{set_id}.json"

    def _run_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def _tests_path(self) -> Path:
        return self.root / "test_sets.json"

    def _write(self, path: Path, payload: dict) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(path)

    def ensure_seeded(self) -> None:
        with self._lock:
            if any(self.sets_dir.glob("*.json")):
                self._ensure_test_sets_unlocked()
                return
            generic = self._new_set(
                topic="Generic",
                description="Fallback Primer Director. Used when no specialized prompt matches.",
                prompt=STORYBOARD_SYSTEM,
                tags=["generic", "fallback"],
                is_generic=True,
                notes="Canonical storyboard director. Topic prompts specialize from this.",
            )
            self._write(self._set_path(generic.set_id), generic.dump())
            for topic, desc, tag, extra in TOPIC_SEEDS:
                body = STORYBOARD_SYSTEM.rstrip() + "\n\n" + extra
                item = self._new_set(
                    topic=topic,
                    description=desc,
                    prompt=body,
                    tags=[tag],
                    is_generic=False,
                    notes=extra.split("\n", 1)[0],
                )
                self._write(self._set_path(item.set_id), item.dump())
            self._ensure_test_sets_unlocked()

    def _ensure_test_sets_unlocked(self) -> None:
        path = self._tests_path()
        if path.exists():
            return
        seeds = [
            TestSet(
                set_id=_id("ts"),
                name="Calculus",
                topic="Calculus",
                questions=[
                    "Why does calculus use limits?",
                    "What is a derivative?",
                    "Why is integration useful?",
                ],
            ),
            TestSet(
                set_id=_id("ts"),
                name="Finance",
                topic="Finance",
                questions=[
                    "Why do bond prices fall when interest rates rise?",
                    "What is compound interest?",
                    "Why does inflation reduce purchasing power?",
                ],
            ),
            TestSet(
                set_id=_id("ts"),
                name="Economics",
                topic="Economics",
                questions=[
                    "Why does inflation reduce purchasing power?",
                    "What is opportunity cost?",
                ],
            ),
            TestSet(
                set_id=_id("ts"),
                name="Technology",
                topic="Technology",
                questions=["How does a neural network learn?"],
            ),
            TestSet(
                set_id=_id("ts"),
                name="Abstract",
                topic="",
                questions=["What is opportunity cost?"],
            ),
        ]
        self._write(path, {"sets": [item.dump() for item in seeds]})

    def _new_set(
        self,
        topic: str,
        description: str,
        prompt: str,
        tags: list[str],
        is_generic: bool,
        notes: str,
    ) -> PromptSet:
        set_id = _id("ps")
        version = PromptVersion(
            version_id=_id("pv"),
            version=1,
            prompt=prompt,
            reason="seed",
        )
        return PromptSet(
            set_id=set_id,
            topic=topic,
            slug="generic" if is_generic else slugify(topic),
            description=description,
            tags=tags,
            is_generic=is_generic,
            notes=notes,
            versions=[version],
        )

    def list_sets(self) -> list[PromptSet]:
        with self._lock:
            items = []
            for path in sorted(self.sets_dir.glob("*.json")):
                try:
                    items.append(PromptSet.model_validate(json.loads(path.read_text())))
                except Exception:
                    continue
            return items

    def get_set(self, set_id: str) -> Optional[PromptSet]:
        with self._lock:
            return self._read_set(set_id)

    def _read_set(self, set_id: str) -> Optional[PromptSet]:
        path = self._set_path(set_id)
        if not path.exists():
            return None
        try:
            return PromptSet.model_validate(json.loads(path.read_text()))
        except Exception:
            return None

    def save_set(self, item: PromptSet) -> PromptSet:
        item.updated_at = _now()
        with self._lock:
            self._write(self._set_path(item.set_id), item.dump())
        return item

    def create_set(
        self,
        topic: str,
        prompt: str,
        description: str = "",
        notes: str = "",
        tags: list[str] | None = None,
        prompt_type: str = "director",
        is_generic: bool = False,
    ) -> PromptSet:
        topic = topic.strip()
        if not topic:
            raise ValueError("topic required")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt required")
        item = self._new_set(
            topic=topic,
            description=description.strip(),
            prompt=prompt,
            tags=tags or [],
            is_generic=is_generic,
            notes=notes,
        )
        item.prompt_type = prompt_type or "director"
        return self.save_set(item)

    def update_set_meta(self, set_id: str, **fields) -> Optional[PromptSet]:
        with self._lock:
            item = self._read_set(set_id)
            if not item:
                return None
            for key in ("topic", "description", "notes", "prompt_type", "active", "tags"):
                if key in fields and fields[key] is not None:
                    if key == "topic":
                        item.topic = str(fields[key]).strip()
                        if not item.is_generic:
                            item.slug = slugify(item.topic)
                    elif key == "tags":
                        item.tags = [str(t).strip() for t in fields[key] if str(t).strip()]
                    else:
                        setattr(item, key, fields[key])
            item.updated_at = _now()
            self._write(self._set_path(set_id), item.dump())
            return item

    def add_version(
        self,
        set_id: str,
        prompt: str,
        reason: str = "",
        parent_version: int = 0,
        run_id: str = "",
        test_question: str = "",
        diagnosis: str = "",
        proposed_changes: list[str] | None = None,
        expected_impact: str = "",
        llm_model: str = "",
        critic_feedback: str = "",
        scores: dict | None = None,
        activate: bool = False,
    ) -> Optional[PromptVersion]:
        with self._lock:
            item = self._read_set(set_id)
            if not item:
                return None
            number = max((v.version for v in item.versions), default=0) + 1
            version = PromptVersion(
                version_id=_id("pv"),
                version=number,
                prompt=prompt,
                parent_version=parent_version or item.active_version,
                reason=reason,
                diagnosis=diagnosis,
                proposed_changes=proposed_changes or [],
                expected_impact=expected_impact,
                llm_model=llm_model,
                run_id=run_id,
                test_question=test_question,
                critic_feedback=critic_feedback,
                scores=scores or {},
            )
            item.versions.append(version)
            if activate:
                item.active_version = number
            item.updated_at = _now()
            self._write(self._set_path(set_id), item.dump())
            return version

    def promote_version(self, set_id: str, version: int) -> Optional[PromptSet]:
        with self._lock:
            item = self._read_set(set_id)
            if not item or not item.version(version):
                return None
            item.active_version = version
            item.updated_at = _now()
            self._write(self._set_path(set_id), item.dump())
            return item

    def record_set_score(self, set_id: str, version: int, score: float) -> None:
        with self._lock:
            item = self._read_set(set_id)
            if not item:
                return
            if score > item.best_score:
                item.best_score = score
                item.best_version = version
            item.updated_at = _now()
            self._write(self._set_path(set_id), item.dump())

    def bump_run_count(self, set_id: str) -> None:
        with self._lock:
            item = self._read_set(set_id)
            if not item:
                return
            item.run_count += 1
            item.updated_at = _now()
            self._write(self._set_path(set_id), item.dump())

    def delete_set(self, set_id: str) -> bool:
        with self._lock:
            item = self._read_set(set_id)
            if not item:
                return False
            if item.is_generic:
                raise ValueError("cannot delete the generic fallback prompt")
            path = self._set_path(set_id)
            path.unlink(missing_ok=True)
            return True

    def list_test_sets(self) -> list[TestSet]:
        with self._lock:
            path = self._tests_path()
            if not path.exists():
                return []
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                return []
            return [TestSet.model_validate(row) for row in data.get("sets") or []]

    def save_test_sets(self, items: list[TestSet]) -> None:
        with self._lock:
            self._write(self._tests_path(), {"sets": [item.dump() for item in items]})

    def create_test_set(self, name: str, questions: list[str], topic: str = "") -> TestSet:
        questions = [q.strip() for q in questions if q.strip()]
        if not name.strip() or not questions:
            raise ValueError("name and at least one question required")
        items = self.list_test_sets()
        item = TestSet(set_id=_id("ts"), name=name.strip(), topic=topic.strip(), questions=questions)
        items.append(item)
        self.save_test_sets(items)
        return item

    def delete_test_set(self, set_id: str) -> bool:
        items = [item for item in self.list_test_sets() if item.set_id != set_id]
        if len(items) == len(self.list_test_sets()):
            return False
        self.save_test_sets(items)
        return True

    def list_runs(self) -> list[EvolutionRun]:
        with self._lock:
            items = []
            for path in self.runs_dir.glob("*.json"):
                try:
                    items.append(EvolutionRun.model_validate(json.loads(path.read_text())))
                except Exception:
                    continue
            items.sort(key=lambda r: r.created_at, reverse=True)
            return items

    def get_run(self, run_id: str) -> Optional[EvolutionRun]:
        with self._lock:
            return self._read_run(run_id)

    def _read_run(self, run_id: str) -> Optional[EvolutionRun]:
        path = self._run_path(run_id)
        if not path.exists():
            return None
        try:
            return EvolutionRun.model_validate(json.loads(path.read_text()))
        except Exception:
            return None

    def save_run(self, run: EvolutionRun) -> EvolutionRun:
        run.updated_at = _now()
        with self._lock:
            self._write(self._run_path(run.run_id), run.dump())
        return run

    def mutate_run(self, run_id: str, fn: Callable[[EvolutionRun], None]) -> Optional[EvolutionRun]:
        with self._lock:
            run = self._read_run(run_id)
            if not run:
                return None
            fn(run)
            run.updated_at = _now()
            self._write(self._run_path(run_id), run.dump())
            return run

    def create_run(self, run: EvolutionRun) -> EvolutionRun:
        return self.save_run(run)


def clamp_max_iterations(value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_MAX_ITERATIONS
    if number < 1:
        raise ValueError("max_iterations must be at least 1")
    if number > HARD_MAX_ITERATIONS:
        raise ValueError(f"max_iterations cannot exceed {HARD_MAX_ITERATIONS}")
    return number


def mean_overall(results: list[QuestionResult]) -> tuple[float, dict]:
    scored = [r for r in results if r.scores]
    if not scored:
        return 0.0, {k: 0.0 for k in SCORE_KEYS}
    averages = {}
    for key in SCORE_KEYS:
        values = [float(r.scores.get(key) or 0) for r in scored]
        averages[key] = round(sum(values) / len(values), 2)
    overall = averages.get("overall_score") or 0.0
    return overall, averages


def should_stop_run(
    *,
    iteration_count: int,
    max_iterations: int,
    stagnant_count: int,
    stagnant_limit: int,
    critic_wants_stop: bool,
    critic_evidence: str,
    has_failure_modes: bool,
    overall: float,
) -> tuple[bool, str]:
    if iteration_count >= max_iterations:
        return True, "max_iterations_reached"
    if stagnant_count >= stagnant_limit and iteration_count >= stagnant_limit:
        return True, "improvement_below_threshold"
    if (
        critic_wants_stop
        and overall >= 8.0
        and not has_failure_modes
        and (critic_evidence or "").strip()
    ):
        return True, "no_meaningful_remaining_improvement"
    return False, ""
