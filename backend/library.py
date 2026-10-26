"""Local library of generated reels and courses.

Primer has no MySQL (or any hosted database). Lessons used to live in the
browser's localStorage, which filled the quota with SVG-heavy reels. SQLite
is the right local store: one file next to the rest of backend/data, no extra
process, and the browser only keeps an id list in memory.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent / "data" / "primer.db"
MAX_LESSONS = 80

_lock = threading.Lock()
_ready = False


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lessons (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    question TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    handwriting TEXT NOT NULL DEFAULT '',
                    reel_json TEXT,
                    curriculum_json TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS lessons_created_at ON lessons(created_at DESC)"
            )
            conn.commit()
        finally:
            conn.close()
        _ready = True


def _row_to_lesson(row: sqlite3.Row) -> dict[str, Any]:
    reel = None
    curriculum = None
    if row["reel_json"]:
        try:
            reel = json.loads(row["reel_json"])
        except json.JSONDecodeError:
            reel = None
    if row["curriculum_json"]:
        try:
            curriculum = json.loads(row["curriculum_json"])
        except json.JSONDecodeError:
            curriculum = None
    item = {
        "id": row["id"],
        "kind": row["kind"] if row["kind"] in {"reel", "course"} else "reel",
        "question": row["question"] or "",
        "title": row["title"] or "",
        "handwriting": row["handwriting"] or "",
        "reel": reel,
        "createdAt": int(row["created_at"] or 0),
    }
    if curriculum:
        item["curriculum"] = curriculum
    return item


def _rows() -> list[dict[str, Any]]:
    _init()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM lessons ORDER BY created_at DESC LIMIT ?",
                (MAX_LESSONS,),
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_lesson(row) for row in rows]


# curriculum id -> file mtime at the last sync. Rewriting every course's JSON
# into SQLite on every list call made opening the app take ~9 seconds.
_synced: dict[str, float] = {}


def _sync_curricula() -> None:
    """Courses live on disk as JSON. Keep the lesson rail in sync, including in-progress ones."""
    from curriculum import CurriculumStore

    store = CurriculumStore.default()
    changed: dict[str, float] = {}
    for path in store.root.glob("*.json"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if _synced.get(path.stem) != mtime:
            changed[path.stem] = mtime
    if not changed:
        return

    known: dict[str, dict[str, Any]] = {}
    for item in _rows():
        cid = (item.get("curriculum") or {}).get("curriculum_id")
        if cid:
            known[str(cid)] = item

    for cid_changed, mtime in changed.items():
        course = store.get(cid_changed)
        _synced[cid_changed] = mtime
        if not course:
            continue
        dumped = course.dump(include_source=False)
        cid = course.curriculum_id
        reels = dumped.get("reels") or []
        ready = sum(1 for item in reels if item.get("status") == "ready")
        building = any(
            item.get("status") in {"pending", "scripted", "generating"} for item in reels
        )
        first = next((item.get("reel") for item in reels if item.get("reel")), None)
        existing = known.get(cid)
        upsert_lesson(
            {
                "id": existing["id"] if existing else f"course-{cid}",
                "kind": "course",
                "question": dumped.get("topic") or course.topic,
                "title": (
                    f"Course · {ready}/{len(reels)} ready"
                    if building
                    else f"Course · {ready}/{len(reels)} lessons"
                ),
                "handwriting": " · ".join(
                    str(item.get("title") or "") for item in reels if item.get("title")
                ),
                "reel": first,
                "curriculum": dumped,
                "createdAt": existing["createdAt"]
                if existing
                else dumped.get("created_at") or course.created_at,
            }
        )


def list_lessons() -> list[dict[str, Any]]:
    _init()
    _sync_curricula()
    return _rows()


def get_lesson(lesson_id: str) -> Optional[dict[str, Any]]:
    _init()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        finally:
            conn.close()
    return _row_to_lesson(row) if row else None


def upsert_lesson(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Insert or replace one lesson, then return the full library (newest first)."""
    _init()
    lesson_id = str(raw.get("id") or "").strip()
    if not lesson_id:
        raise ValueError("id required")
    kind = "course" if raw.get("kind") == "course" else "reel"
    reel = raw.get("reel")
    curriculum = raw.get("curriculum")
    created = raw.get("createdAt") or raw.get("created_at") or 0
    try:
        created_at = float(created)
    except (TypeError, ValueError):
        created_at = 0.0
    if created_at <= 0:
        created_at = time.time() * 1000
    elif created_at < 1e12:
        created_at *= 1000
    payload = (
        lesson_id,
        kind,
        str(raw.get("question") or ""),
        str(raw.get("title") or ""),
        str(raw.get("handwriting") or ""),
        json.dumps(reel, ensure_ascii=False) if reel else None,
        json.dumps(curriculum, ensure_ascii=False) if curriculum else None,
        created_at,
    )
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO lessons (
                    id, kind, question, title, handwriting, reel_json, curriculum_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    question = excluded.question,
                    title = excluded.title,
                    handwriting = excluded.handwriting,
                    reel_json = excluded.reel_json,
                    curriculum_json = excluded.curriculum_json,
                    created_at = lessons.created_at
                """,
                payload,
            )
            conn.execute(
                """
                DELETE FROM lessons WHERE id NOT IN (
                    SELECT id FROM lessons ORDER BY created_at DESC LIMIT ?
                )
                """,
                (MAX_LESSONS,),
            )
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM lessons ORDER BY created_at DESC LIMIT ?",
                (MAX_LESSONS,),
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_lesson(row) for row in rows]


def delete_lesson(lesson_id: str) -> list[dict[str, Any]]:
    _init()
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            conn.commit()
        finally:
            conn.close()
    return list_lessons()


def import_lessons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One-shot migrate from the old browser cache. Newer createdAt wins on id clash."""
    _init()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            upsert_lesson(item)
        except ValueError:
            continue
    return list_lessons()
