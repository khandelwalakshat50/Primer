import { useEffect, useRef, useState, type CSSProperties } from "react";
import { fetchCurriculum, warmNarration, type LlmProvider, type VoiceLang } from "../api";
import { Button } from "../ui";
import { DownloadButton } from "./DownloadButton";
import { GenerationCard, ProgressMeter } from "./ProgressMeter";
import { ReelPlayer } from "./ReelPlayer";
import { useLessonSwipe } from "../useWatchMode";
import { lessonProgress } from "../lessonCache";
import type { Curriculum, CurriculumReelSpec, ReelPlan } from "../types";

const POLL_MS = 2500;

type Props = {
  topic: string;
  voice: boolean;
  lang?: VoiceLang;
  visualEngine?: "svg" | "sora";
  provider?: LlmProvider;
  restored: Curriculum | null;
  onUpdate: (curriculum: Curriculum) => void;
  onExit: () => void;
  onRetry?: (curriculumId: string) => void;
  /** Server's word on the syllabus while the plan is still being made. */
  planningLabel?: string;
  watch?: boolean;
  /** The course is hidden behind another mode; keep its narration quiet. */
  parked?: boolean;
};

function hasPendingWork(curriculum: Curriculum): boolean {
  return curriculum.reels.some(
    (r) => r.status === "pending" || r.status === "scripted" || r.status === "generating",
  );
}

function lessonStage(spec: CurriculumReelSpec): string {
  if (spec.stage) return spec.stage;
  if (spec.status === "scripted") return "waiting";
  if (spec.status === "pending") return "queued";
  return (spec.narration_draft || "").trim() ? "storyboard" : "script";
}

function statusLabel(spec: CurriculumReelSpec): string {
  if (spec.status === "ready") return "Ready";
  if (spec.status === "failed") return spec.error === "Stopped" ? "Stopped" : "Didn't finish";
  if (spec.stage_label) return spec.stage_label;
  if (spec.status === "scripted") return "Script ready · waiting to be drawn";
  if (spec.status === "generating") return "Writing the script";
  return "Queued";
}

export function CurriculumPlayer({
  topic,
  voice,
  lang = "en",
  restored,
  onUpdate,
  onExit,
  onRetry,
  planningLabel = "",
  watch = false,
  parked = false,
}: Props) {
  const [curriculum, setCurriculum] = useState<Curriculum | null>(restored);
  const [index, setIndex] = useState(0);
  const [playId, setPlayId] = useState(0);
  const [lockedPlan, setLockedPlan] = useState<ReelPlan | null>(
    restored?.reels[0]?.reel ?? null,
  );
  const playingKey = useRef("");
  const live = useRef(true);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;
  const swipe = useLessonSwipe(
    () => setIndex((i) => i + 1),
    () => setIndex((i) => Math.max(0, i - 1)),
  );

  function apply(next: Curriculum) {
    onUpdateRef.current(next);
    if (live.current) setCurriculum(next);
  }

  const bootKey = restored?.curriculum_id ?? `new:${topic}`;

  useEffect(() => {
    live.current = true;
    setIndex(0);
    playingKey.current = "";
    setCurriculum(restored);

    if (restored?.curriculum_id) {
      const first = restored.reels.find((r) => r.status === "ready" && r.reel);
      setLockedPlan(first?.reel ?? null);
      if (first) {
        playingKey.current = `0:${first.reel_id}`;
        setPlayId((n) => n + 1);
      }
    }

    return () => {
      live.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootKey]);

  // A retry from outside (the rail, or the button below) reopens the course.
  useEffect(() => {
    if (restored && restored.curriculum_id === curriculum?.curriculum_id) {
      const outside = restored.reels.map((r) => r.status).join(",");
      const inside = curriculum.reels.map((r) => r.status).join(",");
      if (outside !== inside && hasPendingWork(restored)) setCurriculum(restored);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restored]);

  const pending = curriculum ? hasPendingWork(curriculum) : false;

  useEffect(() => {
    if (!curriculum || !pending) return;
    let cancelled = false;
    const cid = curriculum.curriculum_id;
    async function tick() {
      try {
        const fresh = await fetchCurriculum(cid, { resume: true });
        if (!cancelled) apply(fresh);
      } catch {
        /* keep polling */
      }
    }
    void tick();
    const id = window.setInterval(() => {
      void tick();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curriculum?.curriculum_id, pending]);

  useEffect(() => {
    if (!curriculum) return;
    const current = curriculum.reels[index];
    if (current?.status !== "ready" || !current.reel) return;
    const key = `${index}:${current.reel_id}:${lang}:${voice}`;
    const reel = current.reel;
    if (voice) {
      warmNarration(
        [reel.hook, ...reel.scenes, reel.payoff].map((beat) => beat.narration),
        lang,
      );
      const upcoming = curriculum.reels[index + 1]?.reel;
      if (upcoming) {
        warmNarration(
          [upcoming.hook, ...upcoming.scenes, upcoming.payoff].map((beat) => beat.narration),
          lang,
        );
      }
    }
    if (playingKey.current !== key) {
      playingKey.current = key;
      setLockedPlan(current.reel);
      setPlayId((n) => n + 1);
    }
  }, [curriculum, index, lang, voice]);

  if (!curriculum) {
    return (
      <div className="curriculum-shell">
        <ReelPlayer
          plan={null}
          voice={false}
          playId={0}
          bare={watch}
          placeholder={
            <GenerationCard
              title={topic}
              subtitle="Planning your course"
              stage="script"
              label={planningLabel || "Designing the syllabus"}
              progress={0}
              etaKey=""
              indeterminate
            />
          }
        />
      </div>
    );
  }

  const total = curriculum.reels.length;
  const current: CurriculumReelSpec | undefined = curriculum.reels[index];
  const isDone = index >= total;
  const readyReels = curriculum.reels.filter((r) => r.status === "ready" && r.reel);
  const failedCount = curriculum.reels.filter((r) => r.status === "failed").length;
  const overall = curriculum.reels.reduce((sum, r) => sum + lessonProgress(r), 0) / Math.max(1, total);
  const working = curriculum.reels.find(
    (r) => r.status === "generating" || (r.status === "scripted" && r.stage && r.stage !== "waiting"),
  );
  const downloadCourse =
    readyReels.length > 1 ? (
      <DownloadButton
        key={`course:${curriculum.curriculum_id}:${readyReels.length}:${lang}`}
        title={curriculum.topic}
        reels={readyReels.map((r) => r.reel as ReelPlan)}
        lang={lang}
        prepare={false}
        label={
          readyReels.length === total
            ? "Download whole course"
            : `Download ${readyReels.length} ready lessons`
        }
      />
    ) : null;

  if (isDone) {
    return (
      <div className="curriculum-state is-done" {...(watch ? swipe : {})}>
        <p className="curriculum-complete">You've completed {curriculum.topic}.</p>
        {watch ? (
          <p>Slide right for the last lesson.</p>
        ) : (
          <>
            {downloadCourse}
            <Button variant="ghost" onClick={() => setIndex(total - 1)}>
              Back to the last lesson
            </Button>
            <Button onClick={onExit}>Start another course</Button>
          </>
        )}
      </div>
    );
  }

  let placeholder = null;
  if (current && current.status === "failed") {
    placeholder = (
      <div className="gen-card is-failed" role="alert">
        <p className="gen-kicker">
          Lesson {index + 1} · {current.error === "Stopped" ? "stopped" : "didn't finish"}
        </p>
        <p className="gen-title">{current.title}</p>
        {current.error && current.error !== "Stopped" ? (
          <p className="gen-note">{current.error}</p>
        ) : null}
        {onRetry && !watch ? (
          <div className="gen-actions">
            <Button onClick={() => onRetry(curriculum.curriculum_id)}>
              {failedCount > 1 ? `Retry ${failedCount} lessons` : "Retry"}
            </Button>
          </div>
        ) : null}
      </div>
    );
  } else if (current && (current.status !== "ready" || !lockedPlan)) {
    placeholder = (
      <GenerationCard
        title={current.title}
        subtitle={`Lesson ${index + 1} of ${total}`}
        stage={lessonStage(current)}
        label={statusLabel(current)}
        progress={lessonProgress(current)}
        etaKey={`${curriculum.curriculum_id}:${current.reel_id}`}
        note={curriculum.note || undefined}
      >
        {readyReels.length ? (
          <Button
            variant="ghost"
            onClick={() => setIndex(curriculum.reels.findIndex((r) => r.status === "ready"))}
          >
            Watch a ready lesson
          </Button>
        ) : null}
      </GenerationCard>
    );
  }

  const showPlan = current?.status === "ready" && lockedPlan ? lockedPlan : null;

  return (
    <div className={`curriculum-shell${watch ? " is-watch" : ""}`} {...(watch ? swipe : {})}>
      {watch ? null : (
        <div className="course-head">
          <div className="course-head-row">
            <p className="course-title">
              {index + 1} of {total} — {current?.title}
            </p>
            {!pending && failedCount && onRetry ? (
              <Button variant="ghost" className="course-retry" onClick={() => onRetry(curriculum.curriculum_id)}>
                Retry {failedCount} unfinished
              </Button>
            ) : null}
          </div>
          {pending ? (
            <ProgressMeter
              compact
              progress={overall}
              etaKey={curriculum.curriculum_id}
              label={`${readyReels.length} of ${total} lessons ready${
                working ? ` · Lesson ${curriculum.reels.indexOf(working) + 1}: ${statusLabel(working)}` : ""
              }`}
            />
          ) : null}
          {curriculum.note ? <p className="course-note">{curriculum.note}</p> : null}
        </div>
      )}
      <ReelPlayer
        plan={showPlan}
        voice={voice}
        lang={lang}
        playId={playId}
        bare={watch}
        suspended={parked}
        placeholder={placeholder}
        actions={
          showPlan ? (
            <>
              <DownloadButton
                key={`lesson:${current?.reel_id}:${lang}`}
                title={`${curriculum.topic} — ${index + 1}. ${current?.title ?? ""}`}
                reels={[current?.reel ?? showPlan]}
                lang={lang}
                label="Download lesson"
              />
              {downloadCourse}
            </>
          ) : undefined
        }
      />
      {watch ? null : (
        <>
          <nav className="lesson-jump" aria-label="Course lessons">
            {curriculum.reels.map((spec, i) => {
              const p = lessonProgress(spec);
              const state =
                spec.status === "ready" ? "ready" : spec.status === "failed" ? "failed" : "working";
              return (
                <button
                  key={spec.reel_id}
                  type="button"
                  className={`lesson-jump-n is-${state}${i === index ? " is-on" : ""}`}
                  style={{ "--p": `${Math.round(p * 100)}%` } as CSSProperties}
                  onClick={() => setIndex(i)}
                  title={`${spec.title} · ${statusLabel(spec)}`}
                  aria-current={i === index ? "true" : undefined}
                  aria-label={`Lesson ${i + 1}, ${spec.title}, ${statusLabel(spec)}`}
                >
                  {i + 1}
                </button>
              );
            })}
          </nav>
          <Button className="curriculum-next" onClick={() => setIndex((i) => i + 1)}>
            {index + 1 >= total ? "Finish" : "Next lesson"}
          </Button>
        </>
      )}
    </div>
  );
}
