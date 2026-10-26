import type { CachedLesson } from "../types";
import { Button } from "../ui";

/** Live state of a lesson that is still being made (or did not finish). */
export type RailStatus = {
  tone: "run" | "fail";
  progress: number;
  label: string;
  canStop?: boolean;
  canRetry?: boolean;
  canRemove?: boolean;
};

type Props = {
  lessons: CachedLesson[];
  activeId: string | null;
  status: Record<string, RailStatus>;
  onSelect: (id: string) => void;
  onPrevious: () => void;
  onStats?: (id: string) => void;
  onStop?: (id: string) => void;
  onRetry?: (id: string) => void;
  onRemove?: (id: string) => void;
  watch?: boolean;
};

function hasStats(lesson: CachedLesson): boolean {
  if (lesson.reel?.metrics?.steps?.length) return true;
  return Boolean(
    lesson.curriculum?.reels?.some(
      (r) => r.metrics?.steps?.length || r.reel?.metrics?.steps?.length,
    ),
  );
}

export function LessonRail({
  lessons,
  activeId,
  status,
  onSelect,
  onPrevious,
  onStats,
  onStop,
  onRetry,
  onRemove,
  watch = false,
}: Props) {
  const activeIndex = lessons.findIndex((item) => item.id === activeId);
  const canPrevious = activeIndex >= 0 && activeIndex < lessons.length - 1;

  return (
    <aside className={`lesson-rail${watch ? " is-watch" : ""}`}>
      <div className="lesson-rail-head">
        <p className="lesson-rail-title">Lessons</p>
        {watch ? null : (
        <Button
          variant="ghost"
          className="lesson-prev"
          disabled={!canPrevious}
          onClick={onPrevious}
        >
          Previous lesson
        </Button>
        )}
      </div>
      {lessons.length === 0 ? (
        <p className="lesson-rail-empty">Asked questions land here. Replay without using tokens.</p>
      ) : (
        <ul className="lesson-list">
          {lessons.map((lesson) => {
            const live = status[lesson.id];
            return (
              <li key={lesson.id}>
                <button
                  type="button"
                  className={`lesson-item${lesson.id === activeId ? " is-active" : ""}`}
                  onClick={() => onSelect(lesson.id)}
                >
                  <span className="lesson-q">
                    {lesson.kind === "course" ? `Course · ${lesson.question}` : lesson.question}
                  </span>
                  {live ? (
                    <span className={`lesson-live is-${live.tone}`}>
                      <span className="lesson-live-label">
                        {live.tone === "run" ? <span className="lesson-pulse" aria-hidden="true" /> : null}
                        {live.label}
                      </span>
                      {live.tone === "run" ? (
                        <span className="lesson-bar" aria-hidden="true">
                          <span style={{ width: `${Math.round(Math.max(0.03, live.progress) * 100)}%` }} />
                        </span>
                      ) : null}
                    </span>
                  ) : (
                    <span className="lesson-t">{lesson.title}</span>
                  )}
                </button>
                {!watch ? (
                  <div className="lesson-actions">
                    {onStats ? (
                      <button
                        type="button"
                        className="lesson-stats"
                        onClick={() => onStats(lesson.id)}
                        disabled={!hasStats(lesson)}
                        title={
                          hasStats(lesson)
                            ? "Time, tokens and cost for this lesson"
                            : "No production data for this lesson"
                        }
                      >
                        Production stats
                      </button>
                    ) : null}
                    {onRetry && live?.canRetry ? (
                      <button type="button" className="lesson-stop" onClick={() => onRetry(lesson.id)}>
                        Retry
                      </button>
                    ) : null}
                    {onStop && live?.canStop ? (
                      <button
                        type="button"
                        className="lesson-stop"
                        onClick={() => onStop(lesson.id)}
                        title="Stop generation"
                      >
                        Stop
                      </button>
                    ) : null}
                    {onRemove && live?.canRemove ? (
                      <button
                        type="button"
                        className="lesson-stop is-quiet"
                        onClick={() => onRemove(lesson.id)}
                        title="Remove from the list"
                      >
                        Remove
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
