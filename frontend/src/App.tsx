import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createCurriculum, createReelJob, fetchCurriculum, fetchPrimerModels, fetchReelJob, retryReelJob, stopCurriculum, stopCurriculumByTopic, stopReelJob, abortCurriculumTopic, curriculumLock, resumeCurriculum, stopDictation, unlockPlayback, warmNarration, type LlmProvider, type PrimerModels, type VoiceLang } from "./api";
import { ChatBar } from "./components/ChatBar";
import { CurriculumPlayer } from "./components/CurriculumPlayer";
import { DownloadButton } from "./components/DownloadButton";
import { LessonRail, type RailStatus } from "./components/LessonRail";
import { GenerationCard } from "./components/ProgressMeter";
import { PromptLab } from "./components/PromptLab";
import { ReelPlayer } from "./components/ReelPlayer";
import { ProductionPanel } from "./components/ProductionPanel";
import { ScriptPanel } from "./components/ScriptPanel";
import { Button, Segmented } from "./ui";
import { loadLessons, saveLesson, deleteLesson, cacheFromCurriculum, courseIsBuilding, lessonProgress } from "./lessonCache";
import { DEFAULT_FONT_ID, fontById } from "./fonts";
import { FALLBACK_METRICS, paginateText } from "./paginate";
import { useLessonSwipe, useWatchMode } from "./useWatchMode";
import type { CachedLesson, Curriculum, NotebookPage, ReelJob, ReelPlan, Turn } from "./types";

function narrationLines(reel: ReelPlan | null): string[] {
  if (!reel) return [];
  return [reel.hook, ...reel.scenes, reel.payoff]
    .map((beat) => beat.narration)
    .filter((line) => line.trim());
}

type Mode = "chat" | "course" | "lab";
type VoiceMode = "off" | VoiceLang;

function loadVoiceMode(): VoiceMode {
  try {
    const saved = localStorage.getItem("primer-voice-lang");
    if (saved === "off" || saved === "en" || saved === "hi") return saved;
    if (localStorage.getItem("primer-listen") === "0") return "off";
  } catch {
    /* ignore */
  }
  return "en";
}

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function newPage(title = "", text = "", svgs: string[] = []): NotebookPage {
  return { id: newId(), title, text, svgs };
}

function isAbortError(err: unknown): boolean {
  return err instanceof Error && err.name === "AbortError";
}

const REEL_POLL_MS = 2000;
/** A course draft with no plan after this long lost its planning request. */
const STALE_DRAFT_MS = 10 * 60 * 1000;

function jobIsLive(job: ReelJob | undefined): boolean {
  return !job || job.status === "queued" || job.status === "running";
}

function missingJob(lesson: CachedLesson): ReelJob {
  return {
    job_id: lesson.id,
    created_at: lesson.createdAt / 1000,
    updated_at: 0,
    status: "failed",
    stage: "failed",
    stage_label: "Interrupted",
    progress: 0,
    question: lesson.question,
    topic_title: "",
    is_new_topic: false,
    handwriting: "",
    reel: null,
    error: "This reel was cut off before it finished (the old version lost reels when the connection dropped).",
    missing: true,
  };
}

const hand = fontById(DEFAULT_FONT_ID);

export default function App() {
  const [pages, setPages] = useState<NotebookPage[]>(() => [newPage()]);
  const [messages, setMessages] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [voiceLang, setVoiceLang] = useState<VoiceMode>(loadVoiceMode);
  const [sora, setSora] = useState(false);
  const [llm, setLlm] = useState<LlmProvider>(() => {
    try {
      const saved = localStorage.getItem("primer-llm");
      if (saved === "openai" || saved === "gemini" || saved === "claude" || saved === "auto") {
        return saved;
      }
    } catch {
      /* ignore */
    }
    return "auto";
  });
  const [models, setModels] = useState<PrimerModels | null>(null);
  const [lessons, setLessons] = useState<CachedLesson[]>([]);
  const [activeLessonId, setActiveLessonId] = useState<string | null>(null);
  const [reel, setReel] = useState<ReelPlan | null>(null);
  const [playId, setPlayId] = useState(0);
  const [mode, setMode] = useState<Mode>("chat");
  const [courseTopic, setCourseTopic] = useState("");
  const [courseLessonId, setCourseLessonId] = useState<string | null>(null);
  const [restoredCourse, setRestoredCourse] = useState<Curriculum | null>(null);
  const [showScript, setShowScript] = useState(false);
  const [statsLessonId, setStatsLessonId] = useState<string | null>(null);
  const [sourceBusy, setSourceBusy] = useState(false);
  const watch = useWatchMode();
  const [railOpen, setRailOpen] = useState(false);
  const courseLaunch = useRef(false);
  const reelLaunch = useRef(false);
  const liveCourseTopics = useRef(new Set<string>());
  const [reelJobs, setReelJobs] = useState<Record<string, ReelJob>>({});
  const reelJobsRef = useRef(reelJobs);
  reelJobsRef.current = reelJobs;
  const activeIdRef = useRef(activeLessonId);
  activeIdRef.current = activeLessonId;
  /** Jobs started in this tab: their script joins the conversation when done. */
  const sessionJobs = useRef(new Set<string>());
  const [planningLabel, setPlanningLabel] = useState("");

  // In course mode the live curriculum is pushed into the lesson cache by
  // CurriculumPlayer's onUpdate, so that cache entry is the script's source.
  const activeCourse = useMemo(() => {
    if (mode !== "course") return null;
    const cached = lessons.find(
      (item) => item.id === activeLessonId && item.kind === "course" && item.curriculum,
    );
    return cached?.curriculum ?? restoredCourse;
  }, [mode, lessons, activeLessonId, restoredCourse]);

  const hasScript = mode === "course" ? Boolean(activeCourse) : Boolean(reel);

  useEffect(() => {
    if (voiceLang === "off") return;
    warmNarration(narrationLines(reel), voiceLang);
  }, [reel, voiceLang]);

  useEffect(() => {
    void fetchPrimerModels().then(setModels);
  }, []);

  useEffect(() => {
    if (!watch) setRailOpen(false);
  }, [watch]);

  useEffect(() => {
    if (!models) return;
    if (voiceLang === "hi" && !models.has_elevenlabs_key) {
      setVoiceLang("en");
      try {
        localStorage.setItem("primer-voice-lang", "en");
      } catch {
        /* ignore */
      }
    }
  }, [models, voiceLang]);

  useEffect(() => {
    let cancelled = false;
    function applyList(list: CachedLesson[], pickFirst: boolean) {
      setLessons(list);
      if (!pickFirst) return;
      const first = list[0];
      if (!first) return;
      setActiveLessonId((current) => current ?? first.id);
      setReel((current) => current ?? first.reel);
      if (first.kind === "course" && first.curriculum) {
        setMode("course");
        setCourseTopic(first.question);
        setCourseLessonId(first.id);
        setRestoredCourse(first.curriculum);
      }
    }
    void loadLessons()
      .then((list) => {
        if (cancelled) return;
        applyList(list, true);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not load lessons.");
      });
    const onFocus = () => {
      void loadLessons()
        .then((list) => {
          if (!cancelled) applyList(list, false);
        })
        .catch(() => undefined);
    };
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const buildingCourseKey = lessons
    .filter(
      (item) =>
        item.id === activeLessonId &&
        item.kind === "course" &&
        item.curriculum &&
        courseIsBuilding(item.curriculum),
    )
    .map((item) => `${item.id}:${item.curriculum?.curriculum_id}`)
    .join(",");

  useEffect(() => {
    if (!buildingCourseKey) return;
    const building = lessons.filter(
      (item) => item.kind === "course" && item.curriculum && courseIsBuilding(item.curriculum),
    );
    const timer = window.setInterval(() => {
      void (async () => {
        let list: CachedLesson[] | null = null;
        for (const item of building) {
          const cid = item.curriculum?.curriculum_id;
          if (!cid) continue;
          try {
            const fresh = await fetchCurriculum(cid);
            list = await saveLesson(cacheFromCurriculum(fresh, item.id));
          } catch {
            /* keep polling */
          }
        }
        if (list) setLessons(list);
      })();
    }, 4000);
    return () => window.clearInterval(timer);
    // lessons is sampled when the set of in-progress courses changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildingCourseKey]);

  const pendingReelIds = lessons
    .filter((item) => item.kind === "reel" && !item.reel)
    .map((item) => item.id)
    .join(",");

  useEffect(() => {
    if (!pendingReelIds) return;
    let cancelled = false;
    const ids = pendingReelIds.split(",");
    async function tick() {
      const updates: Record<string, ReelJob> = {};
      const finished: ReelJob[] = [];
      await Promise.all(
        ids.map(async (id) => {
          if (!jobIsLive(reelJobsRef.current[id])) return;
          try {
            const job = await fetchReelJob(id);
            if (job) {
              updates[id] = job;
              if (job.status === "ready" && job.reel) finished.push(job);
            } else {
              const lesson = lessons.find((item) => item.id === id);
              if (lesson) updates[id] = missingJob(lesson);
            }
          } catch {
            /* server restarting — keep polling */
          }
        }),
      );
      if (cancelled) return;
      if (Object.keys(updates).length) setReelJobs((prev) => ({ ...prev, ...updates }));
      if (!finished.length) return;
      for (const job of finished) {
        if (sessionJobs.current.has(job.job_id)) {
          sessionJobs.current.delete(job.job_id);
          setMessages((prev) => [...prev, { role: "assistant", content: job.handwriting }]);
          appendToNotebook(job);
        }
        if (job.job_id === activeIdRef.current && job.reel) {
          setReel(job.reel);
          setPlayId((n) => n + 1);
        }
      }
      try {
        const list = await loadLessons();
        if (!cancelled) setLessons(list);
      } catch {
        /* next tick */
      }
    }
    void tick();
    const timer = window.setInterval(() => void tick(), REEL_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // lessons is read for the question of a missing job only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingReelIds]);

  /** The notebook is the model's memory of what it already taught. */
  function appendToNotebook(job: ReelJob) {
    if (!job.handwriting) return;
    setPages((prev) => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      const startFreshPage = job.is_new_topic && Boolean(last.text.trim());
      const seed = startFreshPage
        ? job.handwriting
        : last.text
          ? `${last.text}\n\n${job.handwriting}`
          : job.handwriting;
      const chunks = paginateText(seed, FALLBACK_METRICS, hand.family, hand.size);
      if (startFreshPage) {
        copy.push(newPage(job.topic_title, chunks[0] ?? ""));
      } else {
        copy[copy.length - 1] = {
          ...last,
          title: last.title || job.topic_title,
          text: chunks[0] ?? "",
        };
      }
      for (const chunk of chunks.slice(1)) copy.push(newPage("", chunk));
      return copy;
    });
  }

  const notebookContext = useMemo(
    () =>
      pages
        .filter((p) => p.text.trim())
        .map((p) => `${p.title ? p.title + "\n" : ""}${p.text}`)
        .join("\n\n——\n\n"),
    [pages],
  );

  async function onSend(text: string, opts?: { source?: boolean }) {
    const sourceText = opts?.source ? text : "";
    if (opts?.source || mode === "course") {
      const headline =
        sourceText
          ? text.split("\n").map((line) => line.trim()).find(Boolean)?.slice(0, 80) || "Your text"
          : text;
      const topicNorm = curriculumLock(headline, sourceText);
      const openSame = lessons.find(
        (item) =>
          item.kind === "course" &&
          item.curriculum &&
          courseIsBuilding(item.curriculum) &&
          curriculumLock(item.question) === curriculumLock(headline) &&
          !sourceText,
      );
      if (openSame?.curriculum) {
        setMode("course");
        setActiveLessonId(openSame.id);
        setCourseLessonId(openSame.id);
        setCourseTopic(openSame.question);
        setRestoredCourse(openSame.curriculum);
        return;
      }
      if (busy || courseLaunch.current || liveCourseTopics.current.has(topicNorm)) return;
      courseLaunch.current = true;
      liveCourseTopics.current.add(topicNorm);
      setBusy(true);
      setSourceBusy(Boolean(sourceText));
      setError("");
      stopDictation();
      setMode("course");
      setRestoredCourse(null);
      const draftId = `course-${newId()}`;
      const stub: CachedLesson = {
        id: draftId,
        kind: "course",
        question: headline,
        title: sourceText ? "Course from your text…" : "Course · planning…",
        handwriting: "",
        reel: null,
        createdAt: Date.now(),
      };
      setActiveLessonId(draftId);
      setCourseLessonId(draftId);
      setCourseTopic(headline);
      void saveLesson(stub).then(setLessons).catch(() => undefined);

      const applyCourse = (c: Curriculum) => {
        if (!courseIsBuilding(c)) {
          liveCourseTopics.current.delete(topicNorm);
        }
        setRestoredCourse(c);
        const canonical = `course-${c.curriculum_id}`;
        const lesson = cacheFromCurriculum(c, canonical);
        setCourseLessonId(canonical);
        setActiveLessonId((current) =>
          current === draftId || current === canonical || current == null ? canonical : current,
        );
        void (async () => {
          try {
            await deleteLesson(draftId);
          } catch {
            /* keep going */
          }
          const list = await saveLesson(lesson);
          setLessons(list);
        })().catch(() => undefined);
      };

      try {
        setPlanningLabel("Designing the syllabus");
        await createCurriculum(
          headline,
          { onPlan: applyCourse, onReelReady: applyCourse, onProgress: setPlanningLabel },
          sora ? "sora" : "svg",
          llm,
          sourceText,
        );
      } catch (err) {
        if (isAbortError(err)) return;
        liveCourseTopics.current.delete(topicNorm);
        setError(err instanceof Error ? err.message : "Could not build the course.");
        setCourseTopic("");
        setRestoredCourse(null);
      } finally {
        courseLaunch.current = false;
        setSourceBusy(false);
        setBusy(false);
        setPlanningLabel("");
      }
      return;
    }

    if (reelLaunch.current) return;
    reelLaunch.current = true;
    const nextMessages: Turn[] = [...messages, { role: "user", content: text }];
    setMessages(nextMessages);
    setBusy(true);
    setError("");
    stopDictation();
    const current = pages[pages.length - 1];
    try {
      const job = await createReelJob({
        messages: nextMessages,
        notebook: notebookContext,
        current_topic: current.title,
        visual_engine: sora ? "sora" : "svg",
        provider: llm,
      });
      sessionJobs.current.add(job.job_id);
      setReelJobs((prev) => ({ ...prev, [job.job_id]: job }));
      setMode("chat");
      setActiveLessonId(job.job_id);
      setReel(null);
      setLessons(await loadLessons());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the reel.");
    } finally {
      reelLaunch.current = false;
      setBusy(false);
    }
  }

  async function retryReel(lessonId: string) {
    const lesson = lessons.find((item) => item.id === lessonId);
    if (!lesson) return;
    setError("");
    const known = reelJobs[lessonId];
    try {
      if (known?.missing) {
        // Nothing on the server to resume: ask the same question again.
        const job = await createReelJob({
          messages: [{ role: "user", content: lesson.question }],
          notebook: "",
          current_topic: "",
          visual_engine: sora ? "sora" : "svg",
          provider: llm,
        });
        setReelJobs((prev) => ({ ...prev, [job.job_id]: job }));
        await deleteLesson(lessonId).catch(() => undefined);
        setActiveLessonId(job.job_id);
        setLessons(await loadLessons());
        return;
      }
      const job = await retryReelJob(lessonId);
      setReelJobs((prev) => ({ ...prev, [lessonId]: job }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not retry the reel.");
    }
  }

  async function retryCourse(curriculumId: string) {
    setError("");
    try {
      const course = await resumeCurriculum(curriculumId);
      setRestoredCourse(course);
      setLessons(await saveLesson(cacheFromCurriculum(course, `course-${course.curriculum_id}`)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resume the course.");
    }
  }

  async function removeLesson(lessonId: string) {
    try {
      const list = await deleteLesson(lessonId);
      setLessons(list);
      if (activeLessonId === lessonId) {
        setActiveLessonId(null);
        setReel(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the lesson.");
    }
  }

  async function stopGeneration(lessonId: string) {
    const lesson = lessons.find((item) => item.id === lessonId);
    if (!lesson) return;
    setError("");
    if (lesson.kind === "course") {
      abortCurriculumTopic(lesson.question);
      liveCourseTopics.current.delete(
        lesson.question.trim().toLowerCase().replace(/\s+/g, " "),
      );
      const cid = lesson.curriculum?.curriculum_id;
      try {
        const stopped = cid
          ? await stopCurriculum(cid)
          : await stopCurriculumByTopic(lesson.question);
        const list = await saveLesson(
          cacheFromCurriculum(
            stopped,
            cid && lesson.id.startsWith("course-") ? `course-${cid}` : lesson.id,
          ),
        );
        setLessons(list);
        setRestoredCourse(stopped);
      } catch (err) {
        if (!(err instanceof Error && /404|No in-flight/.test(err.message))) {
          setError(err instanceof Error ? err.message : "Could not stop generation.");
        }
      }
      courseLaunch.current = false;
      setBusy(false);
      return;
    }
    try {
      const job = await stopReelJob(lessonId);
      setReelJobs((prev) => ({ ...prev, [lessonId]: job }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not stop the reel.");
    }
  }

  function openLesson(id: string) {
    const lesson = lessons.find((item) => item.id === id);
    if (!lesson) return;
    stopDictation();
    setActiveLessonId(id);
    if (lesson.kind === "course") {
      setMode("course");
      setCourseTopic(lesson.question);
      setCourseLessonId(lesson.id);
      setRestoredCourse(lesson.curriculum ?? null);
      return;
    }
    setMode("chat");
    if (voiceLang !== "off" && lesson.reel) {
      warmNarration(narrationLines(lesson.reel), voiceLang);
    }
    setReel(lesson.reel);
    setPlayId((n) => n + 1);
  }

  function openPreviousLesson() {
    const index = lessons.findIndex((item) => item.id === activeLessonId);
    if (index < 0 || index >= lessons.length - 1) return;
    openLesson(lessons[index + 1].id);
  }

  function openAdjacentLesson(delta: number) {
    if (!lessons.length) return;
    const index = lessons.findIndex((item) => item.id === activeLessonId);
    const from = index < 0 ? (delta > 0 ? -1 : 0) : index;
    const next = from + delta;
    if (next < 0 || next >= lessons.length) return;
    openLesson(lessons[next].id);
  }

  const swipe = useLessonSwipe(
    () => openAdjacentLesson(1),
    () => openAdjacentLesson(-1),
  );

  const railStatus = useMemo(() => {
    const out: Record<string, RailStatus> = {};
    const now = Date.now();
    for (const lesson of lessons) {
      if (lesson.kind === "reel") {
        if (lesson.reel) continue;
        const job = reelJobs[lesson.id];
        if (!job) {
          out[lesson.id] = { tone: "run", progress: 0, label: "Checking…" };
        } else if (job.status === "failed" || job.status === "stopped") {
          out[lesson.id] = {
            tone: "fail",
            progress: job.progress,
            label: job.status === "stopped" ? "Stopped" : job.missing ? "Interrupted" : "Failed",
            canRetry: true,
            canRemove: true,
          };
        } else {
          out[lesson.id] = {
            tone: "run",
            progress: job.progress,
            label: job.stage_label || "Queued",
            canStop: true,
          };
        }
        continue;
      }
      const course = lesson.curriculum;
      if (!course) {
        const stale = now - lesson.createdAt > STALE_DRAFT_MS;
        out[lesson.id] = stale
          ? { tone: "fail", progress: 0, label: "Planning was interrupted", canRemove: true }
          : { tone: "run", progress: 0, label: "Designing the syllabus", canStop: true };
        continue;
      }
      const total = course.reels.length || 1;
      const ready = course.reels.filter((r) => r.status === "ready").length;
      const failed = course.reels.filter((r) => r.status === "failed").length;
      const progress = course.reels.reduce((sum, r) => sum + lessonProgress(r), 0) / total;
      if (courseIsBuilding(course)) {
        out[lesson.id] = {
          tone: "run",
          progress,
          label: `${ready} of ${total} lessons ready`,
          canStop: true,
        };
      } else if (failed) {
        out[lesson.id] = {
          tone: "fail",
          progress,
          label: `${failed} lesson${failed === 1 ? "" : "s"} didn't finish`,
          canRetry: true,
        };
      }
    }
    return out;
  }, [lessons, reelJobs]);

  const activeLesson = lessons.find((item) => item.id === activeLessonId) ?? null;
  const pendingLesson =
    mode !== "course" && activeLesson?.kind === "reel" && !activeLesson.reel ? activeLesson : null;
  const pendingJob = pendingLesson ? reelJobs[pendingLesson.id] : undefined;
  const shownReel = pendingLesson ? null : reel;
  const voiceCode: VoiceLang = voiceLang === "hi" ? "hi" : "en";

  let reelPlaceholder: ReactNode = undefined;
  if (pendingLesson) {
    const failed = pendingJob && !jobIsLive(pendingJob);
    reelPlaceholder = failed ? (
      <div className="gen-card is-failed" role="alert">
        <p className="gen-kicker">{pendingJob.status === "stopped" ? "Stopped" : "This reel didn't finish"}</p>
        <p className="gen-title">{pendingLesson.question}</p>
        {pendingJob.error ? <p className="gen-note">{pendingJob.error}</p> : null}
        <div className="gen-actions">
          <Button onClick={() => void retryReel(pendingLesson.id)}>
            {pendingJob.missing ? "Ask again" : "Retry"}
          </Button>
          <Button variant="ghost" onClick={() => void removeLesson(pendingLesson.id)}>
            Remove
          </Button>
        </div>
      </div>
    ) : (
      <GenerationCard
        title={pendingLesson.question}
        stage={pendingJob?.stage ?? "queued"}
        label={pendingJob?.stage_label || "Starting"}
        progress={pendingJob?.progress ?? 0}
        etaKey={pendingLesson.id}
        indeterminate={!pendingJob}
      >
        {pendingJob && !watch ? (
          <Button variant="ghost" onClick={() => void stopGeneration(pendingLesson.id)}>
            Stop
          </Button>
        ) : null}
      </GenerationCard>
    );
  }

  return (
    <div
      className={`desk is-reel ${mode === "lab" ? "is-lab" : ""}${watch ? " is-watch" : ""}${watch && railOpen ? " is-rail-open" : ""}`}
      style={
        {
          "--hand": hand.family,
          "--hand-size": hand.size,
        } as CSSProperties
      }
    >
      {watch ? null : (
      <header className="mast">
        <div className="mast-brand">
          <p className="mast-mark">Primer</p>
          <p className="mast-sub">
            {mode === "lab"
              ? "Prompt Evolution Lab"
              : mode === "course"
                ? courseTopic || "Build a course"
                : reel
                  ? reel.title
                  : "Educational reel"}
          </p>
        </div>
        <div className="mast-actions">
          <div className="mast-action-row">
            <Segmented
              ariaLabel="App mode"
              value={mode}
              onChange={(next) => {
                stopDictation();
                setMode(next);
              }}
              options={[
                { id: "chat", label: "Reel" },
                { id: "course", label: "Course" },
                { id: "lab", label: "Lab" },
              ]}
            />
            <Segmented
              ariaLabel="AI model"
              value={llm}
              onChange={(next) => {
                setLlm(next);
                try {
                  localStorage.setItem("primer-llm", next);
                } catch {
                  /* ignore */
                }
              }}
              options={[
                { id: "auto", label: "Auto" },
                { id: "openai", label: "OpenAI", disabled: models?.has_openai_key === false },
                { id: "gemini", label: "Gemini", disabled: models?.has_gemini_key === false },
                { id: "claude", label: "Claude", disabled: models?.has_claude_key === false },
              ]}
            />
            <Segmented
              ariaLabel="Visual engine"
              value={sora ? "sora" : "svg"}
              onChange={(next) => setSora(next === "sora")}
              options={[
                { id: "svg", label: "SVG" },
                { id: "sora", label: "Sora 2" },
              ]}
            />
            <Segmented
              ariaLabel="Voice"
              value={voiceLang}
              onChange={(next) => {
                const mode = next as VoiceMode;
                setVoiceLang(mode);
                try {
                  localStorage.setItem("primer-voice-lang", mode);
                } catch {
                  /* ignore */
                }
                if (mode === "off") stopDictation();
                else setPlayId((n) => n + 1);
              }}
              options={[
                { id: "off", label: "Mute" },
                { id: "en", label: "English" },
                {
                  id: "hi",
                  label: "Hindi",
                  disabled: Boolean(models) && !models.has_elevenlabs_key,
                },
              ]}
            />
          </div>
        </div>
      </header>
      )}

      <main className={`desk-main ${mode === "lab" ? "is-lab" : ""}`}>
        {mode === "lab" && !watch ? (
          <PromptLab />
        ) : (
          <>
        {watch ? (
          <button
            type="button"
            className={`rail-menu${railOpen ? " is-open" : ""}`}
            aria-label={railOpen ? "Close lessons" : "Open lessons"}
            aria-expanded={railOpen}
            onClick={() => {
              unlockPlayback();
              setRailOpen((open) => !open);
            }}
          >
            <span />
            <span />
            <span />
          </button>
        ) : null}
        {watch && railOpen ? (
          <button
            type="button"
            className="rail-scrim"
            aria-label="Close lessons"
            onClick={() => setRailOpen(false)}
          />
        ) : null}
        <LessonRail
          lessons={lessons}
          activeId={activeLessonId}
          status={railStatus}
          onSelect={(id) => {
            openLesson(id);
            if (watch) setRailOpen(false);
          }}
          onPrevious={openPreviousLesson}
          onStats={watch ? undefined : setStatsLessonId}
          onStop={watch ? undefined : stopGeneration}
          onRetry={
            watch
              ? undefined
              : (id) => {
                  const lesson = lessons.find((item) => item.id === id);
                  const cid = lesson?.curriculum?.curriculum_id;
                  if (lesson?.kind === "course" && cid) void retryCourse(cid);
                  else void retryReel(id);
                }
          }
          onRemove={watch ? undefined : (id) => void removeLesson(id)}
          watch={watch}
        />
        {mode !== "course" ? (
          <div className="reel-swipe" {...(watch ? swipe : {})}>
          <ReelPlayer
            plan={shownReel}
            voice={voiceLang !== "off"}
            lang={voiceCode}
            playId={playId}
            bare={watch}
            placeholder={reelPlaceholder}
            actions={
              shownReel ? (
                <DownloadButton
                  key={`${activeLessonId}:${voiceCode}`}
                  title={shownReel.title}
                  reels={[shownReel]}
                  lang={voiceCode}
                />
              ) : undefined
            }
          />
          </div>
        ) : !courseTopic ? (
          <div className="curriculum-state">
            <p>{watch ? "Pick a lesson from the list." : "Type a topic, or tap My text and paste a document to turn it into a course."}</p>
          </div>
        ) : null}
          </>
        )}
        {courseTopic ? (
          <div className={mode === "course" ? "curriculum-host" : "curriculum-host is-parked"}>
            <CurriculumPlayer
              topic={courseTopic}
              voice={voiceLang !== "off"}
              lang={voiceLang === "hi" ? "hi" : "en"}
              visualEngine={sora ? "sora" : "svg"}
              provider={llm}
              restored={restoredCourse}
              watch={watch}
              planningLabel={planningLabel}
              parked={mode !== "course"}
              onRetry={(cid) => void retryCourse(cid)}
              onUpdate={(c) => {
                if (!courseIsBuilding(c)) {
                  liveCourseTopics.current.delete(
                    c.topic.trim().toLowerCase().replace(/\s+/g, " "),
                  );
                }
                setRestoredCourse(c);
                const canonical = `course-${c.curriculum_id}`;
                const lesson = cacheFromCurriculum(c, canonical);
                const draftId = courseLessonId;
                setCourseLessonId(canonical);
                setActiveLessonId((current) =>
                  current === draftId || current === canonical || current == null
                    ? canonical
                    : current,
                );
                void (async () => {
                  if (draftId && draftId !== canonical) {
                    try {
                      await deleteLesson(draftId);
                    } catch {
                      /* keep going */
                    }
                  }
                  const list = await saveLesson(lesson);
                  setLessons(list);
                })().catch(() => undefined);
              }}
              onExit={() => {
                setCourseTopic("");
                setCourseLessonId(null);
                setRestoredCourse(null);
              }}
            />
          </div>
        ) : null}
      </main>

      {watch || mode === "lab" ? null : (
      <footer className="desk-foot is-spread">
        {error ? <p className="desk-error">{error}</p> : null}
        {busy && sourceBusy ? (
          <p className="desk-status">Turning your text into a course…</p>
        ) : null}
        <ChatBar disabled={busy} onSend={onSend} />
      </footer>
      )}

      {watch || mode === "lab" ? null : (
      <Button
        variant="ghost"
        className="script-open"
        onClick={() => setShowScript(true)}
        disabled={!hasScript}
        title={hasScript ? "View the full script" : "No script yet — ask a question first"}
      >
        Script
      </Button>
      )}

      {watch || !statsLessonId ? null : (
        <ProductionPanel
          lesson={lessons.find((item) => item.id === statsLessonId) ?? null}
          onClose={() => setStatsLessonId(null)}
          onStop={
            statsLessonId
              ? () => {
                  void stopGeneration(statsLessonId);
                }
              : undefined
          }
        />
      )}

      {watch || !showScript ? null : (
        <ScriptPanel
          reel={mode === "course" ? null : reel}
          curriculum={activeCourse}
          onClose={() => setShowScript(false)}
        />
      )}
    </div>
  );
}
