import type { CachedLesson, Curriculum, CurriculumReelSpec } from "./types";

function normalize(item: CachedLesson): CachedLesson {
  return {
    ...item,
    kind: item.kind === "course" ? "course" : "reel",
    reel: item.reel ?? null,
  };
}

async function parseLibrary(res: Response): Promise<CachedLesson[]> {
  if (!res.ok) throw new Error(`Library request failed (${res.status})`);
  const data = await res.json();
  const lessons = Array.isArray(data.lessons) ? data.lessons : [];
  return lessons.map((item: CachedLesson) => normalize(item));
}

export async function loadLessons(): Promise<CachedLesson[]> {
  const local = readLegacyCache();
  if (local.length) {
    try {
      const res = await fetch("/api/lessons/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lessons: local }),
      });
      const lessons = await parseLibrary(res);
      clearLegacyCache();
      return lessons;
    } catch {
      /* fall through to the server copy */
    }
  }
  // The server folds every course on disk into this list itself.
  return parseLibrary(await fetch("/api/lessons"));
}

export async function saveLesson(lesson: CachedLesson): Promise<CachedLesson[]> {
  const res = await fetch("/api/lessons", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(normalize(lesson)),
  });
  return parseLibrary(res);
}

export async function deleteLesson(lessonId: string): Promise<CachedLesson[]> {
  const res = await fetch(`/api/lessons/${encodeURIComponent(lessonId)}`, { method: "DELETE" });
  return parseLibrary(res);
}

export function cacheFromCurriculum(curriculum: Curriculum, lessonId?: string): CachedLesson {
  const ready = curriculum.reels.filter((r) => r.status === "ready").length;
  const total = curriculum.reels.length;
  const building = courseIsBuilding(curriculum);
  const createdMs =
    curriculum.created_at > 1e12 ? curriculum.created_at : curriculum.created_at * 1000;
  return {
    id: lessonId || `course-${curriculum.curriculum_id}`,
    kind: "course",
    question: curriculum.topic,
    title: building
      ? `Course · ${ready}/${total} ready`
      : `Course · ${ready}/${total} lessons`,
    handwriting: curriculum.reels.map((r) => r.title).join(" · "),
    reel: curriculum.reels.find((r) => r.reel)?.reel ?? null,
    curriculum,
    createdAt: createdMs || Date.now(),
  };
}

/** 0..1 for one lesson. Older courses have no progress field, so infer a floor. */
export function lessonProgress(spec: CurriculumReelSpec): number {
  if (spec.status === "ready") return 1;
  if (spec.status === "failed") return 0;
  const scripted = Boolean((spec.narration_draft || "").trim());
  return Math.max(spec.progress ?? 0, scripted ? 0.15 : 0);
}

export function courseIsBuilding(curriculum: Curriculum): boolean {
  if (curriculum.status === "planning" || curriculum.status === "generating") return true;
  return curriculum.reels.some(
    (r) => r.status === "pending" || r.status === "scripted" || r.status === "generating",
  );
}

const LEGACY_KEY = "primer-lesson-cache";

function readLegacyCache(): CachedLesson[] {
  try {
    const raw = localStorage.getItem(LEGACY_KEY);
    if (!raw) return [];
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function clearLegacyCache() {
  try {
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* ignore */
  }
}
