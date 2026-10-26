import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  attachNarration,
  audioReady,
  exportFileUrl,
  lookupExport,
  startNarration,
  stopDictation,
  unlockPlayback,
} from "../api";
import {
  HOLD_BUFFER_MS,
  audioDurationMs,
  holdDurationMs,
  plannedDurationMs,
  type BeatTiming,
} from "../timing";
import type { ReelBeat, ReelElement, ReelPlan } from "../types";
import { playFresh } from "../svgMotion";

/** Python's ElementTree often emits ns0:svg, which browsers won't treat as SVG
 *  and which would otherwise render as a wall of XML in a <p>. */
function sanitizeSvgMarkup(raw: string): string {
  return raw
    .replace(/\sxmlns:ns\d+=(['"]).*?\1/g, "")
    .replace(/<\/ns\d+:/g, "</")
    .replace(/<ns\d+:/g, "<")
    .replace(/\sstroke-dasharray=(['"]).*?\1/g, "")
    .replace(/\sstroke-dashoffset=(['"]).*?\1/g, "");
}

function looksLikeSvg(raw: string): boolean {
  return /<(?:[\w.]+:)?svg\b/i.test(raw);
}

function AnimatedDiagram({ content, budgetMs }: { content: string; budgetMs: number }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const svg = root.querySelector("svg");
    if (!svg) return;
    playFresh(svg, {
      budgetMs,
      beatIndex: 0,
      isPaused: () => false,
      isDead: () => false,
      debug: false,
    });
  }, [content, budgetMs]);

  return (
    <div
      className="reel-svg reel-camera"
      ref={ref}
      style={{ "--camera-duration": `${Math.max(900, budgetMs)}ms` } as React.CSSProperties}
      dangerouslySetInnerHTML={{ __html: sanitizeSvgMarkup(content) }}
    />
  );
}

function KineticHeading({
  text,
  emphasis,
  kind,
  place,
}: {
  text: string;
  emphasis: string;
  kind: string;
  place?: string;
}) {
  const placeClass = place ? ` place-${place}` : "";
  const words = text.trim().split(/\s+/).filter(Boolean);
  const wordMode = words.length >= 2 && words.length <= 4;
  if (!wordMode) {
    return <p className={`reel-copy is-${emphasis} is-${kind} kinetic-fade${placeClass}`}>{text}</p>;
  }
  return (
    <p className={`reel-copy is-${emphasis} is-${kind} kinetic-words${placeClass}`}>
      {words.map((word, i) => (
        <span
          key={i}
          className="kinetic-word"
          style={{ animationDelay: `${i * 80}ms` }}
        >
          {word}
          {i < words.length - 1 ? " " : ""}
        </span>
      ))}
    </p>
  );
}

function flatten(plan: ReelPlan): ReelBeat[] {
  return [plan.hook, ...plan.scenes, plan.payoff].filter(
    (beat) => beat.narration.trim() || beat.on_screen.length > 0,
  );
}

function PhoneChrome() {
  return (
    <svg className="phone-vector" viewBox="0 0 390 844" aria-hidden="true">
      <defs>
        <linearGradient id="pro-shell" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3A3A3A" />
          <stop offset="40%" stopColor="#171717" />
          <stop offset="100%" stopColor="#0A0A0A" />
        </linearGradient>
        <linearGradient id="pro-side" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#525252" />
          <stop offset="50%" stopColor="#262626" />
          <stop offset="100%" stopColor="#171717" />
        </linearGradient>
        <linearGradient id="pro-bezel" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#1F1F1F" />
          <stop offset="100%" stopColor="#050505" />
        </linearGradient>
        <linearGradient id="island-glass" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#1a1a1c" />
          <stop offset="100%" stopColor="#050506" />
        </linearGradient>
      </defs>
      <rect x="0" y="178" width="7" height="28" rx="2.5" fill="url(#pro-side)" />
      <rect x="0" y="228" width="7" height="58" rx="2.5" fill="url(#pro-side)" />
      <rect x="0" y="298" width="7" height="58" rx="2.5" fill="url(#pro-side)" />
      <rect x="383" y="248" width="7" height="78" rx="3" fill="url(#pro-side)" />
      <rect x="383" y="348" width="8" height="52" rx="4" fill="url(#pro-side)" />
      <path
        fillRule="evenodd"
        fill="url(#pro-shell)"
        d="M56 5H334A51 51 0 0 1 385 56V788A51 51 0 0 1 334 839H56A51 51 0 0 1 5 788V56A51 51 0 0 1 56 5ZM22 62H368V778H22Z"
      />
      <path
        fill="none"
        stroke="#FFFFFF"
        strokeOpacity="0.18"
        strokeWidth="1.2"
        d="M56 6.2H334A49.8 49.8 0 0 1 383.8 56V788A49.8 49.8 0 0 1 334 837.8H56A49.8 49.8 0 0 1 6.2 788V56A49.8 49.8 0 0 1 56 6.2"
      />
      <path
        fillRule="evenodd"
        fill="url(#pro-bezel)"
        d="M60 11H330A47 47 0 0 1 377 58V786A47 47 0 0 1 330 833H60A47 47 0 0 1 13 786V58A47 47 0 0 1 60 11ZM22 62H368V778H22Z"
      />
      <rect x="126" y="20" width="138" height="34" rx="17" fill="url(#island-glass)" />
      <circle cx="238" cy="37" r="6" fill="#111111" />
      <circle cx="238" cy="37" r="3.1" fill="#404040" />
      <circle cx="238" cy="37" r="1.2" fill="#D4D4D4" />
      <rect x="152" y="800" width="86" height="4.5" rx="2.2" fill="#FAFAFA" opacity="0.35" />
    </svg>
  );
}

function Arrow({ direction }: { direction: string }) {
  const glyph =
    direction === "up"
      ? "↑"
      : direction === "left"
        ? "←"
        : direction === "right"
          ? "→"
          : "↓";
  return <div className={`reel-arrow reel-arrow-${direction || "down"}`}>{glyph}</div>;
}

function StageItem({
  item,
  isFirst,
  budgetMs,
}: {
  item: ReelElement;
  isFirst: boolean;
  budgetMs: number;
}) {
  if (item.type === "arrow") {
    return <Arrow direction={item.direction} />;
  }
  if (item.type === "svg" && looksLikeSvg(item.content)) {
    return <AnimatedDiagram content={item.content} budgetMs={budgetMs} />;
  }
  const place = item.place || "";
  if (item.type === "text" || item.type === "stat") {
    if (isFirst) {
      return (
        <KineticHeading text={item.content} emphasis={item.emphasis} kind={item.type} place={place} />
      );
    }
    return (
      <p className={`reel-copy is-${item.emphasis} is-${item.type}${place ? ` place-${place}` : ""}`}>
        {item.content}
      </p>
    );
  }
  return (
    <p className={`reel-copy is-${item.emphasis} is-${item.type}`}>
      {item.content}
    </p>
  );
}

type Props = {
  plan: ReelPlan | null;
  voice: boolean;
  lang?: "en" | "hi";
  playId: number;
  bare?: boolean;
  /** Shown on the screen when there is no reel yet (e.g. generation progress). */
  placeholder?: ReactNode;
  /** Controls under the phone, e.g. the download button. */
  actions?: ReactNode;
  /** Hidden (e.g. a parked course): hold playback so two narrations never overlap. */
  suspended?: boolean;
};

export function ReelPlayer({
  plan,
  voice,
  lang = "en",
  playId,
  bare = false,
  placeholder,
  actions,
  suspended = false,
}: Props) {
  const [beatIndex, setBeatIndex] = useState(0);
  const [visible, setVisible] = useState(0);
  const [phase, setPhase] = useState<"idle" | "playing" | "done">("idle");
  const [timing, setTiming] = useState<BeatTiming | null>(null);
  const [itemBudgets, setItemBudgets] = useState<number[]>([]);
  const [paused, setPaused] = useState(false);
  const [session, setSession] = useState(0);
  const timers = useRef<number[]>([]);
  const generation = useRef(0);
  const pausedRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const skipRef = useRef(0);
  const seekRef = useRef<number | null>(null);
  const startAtRef = useRef(0);
  const beatIndexRef = useRef(0);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const suspendedRef = useRef(suspended);
  suspendedRef.current = suspended;
  const storedRef = useRef<HTMLVideoElement>(null);
  /** The browser only allowed the saved video to start muted. */
  const [needsSound, setNeedsSound] = useState(false);

  // A reel that has been rendered to MP4 plays that file: voice and picture
  // are baked together, and nothing is fetched or re-drawn. Live playback is
  // only for reels whose video is not made yet (or Hindi / muted choices the
  // stored file does not cover).
  const [stored, setStored] = useState<{ plan: ReelPlan; lang: string; url: string | null } | null>(null);
  useEffect(() => {
    if (!plan) return;
    let off = false;
    const settle = (url: string | null) => {
      if (off) return;
      setStored((prev) => (prev && prev.plan === plan && prev.lang === lang ? prev : { plan, lang, url }));
    };
    // Never hold the first frame long for this check.
    const fallback = window.setTimeout(() => settle(null), 1200);
    lookupExport({ reels: [plan], lang, title: plan.title, prepare: false })
      .then((job) => settle(job?.status === "done" ? exportFileUrl(job.export_id, "reel.mp4") : null))
      .catch(() => settle(null));
    return () => {
      off = true;
      window.clearTimeout(fallback);
    };
  }, [plan, lang]);
  const resolved = plan && stored && stored.plan === plan && stored.lang === lang ? stored : null;
  const videoSrc = resolved?.url ?? null;

  function setPausedState(value: boolean) {
    pausedRef.current = value;
    setPaused(value);
    const audio = audioRef.current;
    if (!audio) return;
    if (value) {
      audio.pause();
    } else if (!audio.ended) {
      void audio.play().catch(() => undefined);
    }
  }

  function pauseReel() {
    if (phaseRef.current !== "playing" || pausedRef.current) return;
    setPausedState(true);
  }

  function playReel() {
    if (!plan) return;
    if (phaseRef.current === "playing") {
      if (pausedRef.current) setPausedState(false);
      return;
    }
    startAtRef.current = phaseRef.current === "done" ? 0 : beatIndexRef.current;
    setSession((n) => n + 1);
  }

  function onScreenToggle() {
    unlockPlayback();
    if (!plan) return;
    const film = storedRef.current;
    if (videoSrc && film) {
      if (needsSound) {
        film.muted = false;
        setNeedsSound(false);
        if (film.paused) void film.play().catch(() => setPaused(true));
        return;
      }
      if (film.ended || phaseRef.current === "done") {
        film.currentTime = 0;
        setPhase("playing");
        phaseRef.current = "playing";
      }
      if (film.paused) {
        // A tap is permission for sound, even if autoplay had to start muted.
        film.muted = !voice;
        setNeedsSound(false);
        void film.play().catch(() => setPaused(true));
      } else film.pause();
      return;
    }
    const audio = audioRef.current;
    const audioWaiting = Boolean(audio && audio.paused && !audio.ended);
    if (phaseRef.current === "playing") {
      if (pausedRef.current || audioWaiting) {
        setPausedState(false);
        return;
      }
      pauseReel();
      return;
    }
    playReel();
  }

  useEffect(() => {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
    stopDictation();
    if (plan && (!resolved || videoSrc)) {
      // Waiting to learn whether a stored video exists, or playing it.
      return;
    }
    if (!plan) {
      setPhase("idle");
      setBeatIndex(0);
      beatIndexRef.current = 0;
      setVisible(0);
      setTiming(null);
      setItemBudgets([]);
      return;
    }

    const beats = flatten(plan);
    if (!beats.length) return;
    const token = ++generation.current;
    const startAt = Math.max(0, Math.min(beats.length - 1, startAtRef.current));
    startAtRef.current = 0;
    skipRef.current += 1;
    seekRef.current = null;
    setPhase("playing");
    phaseRef.current = "playing";
    setBeatIndex(startAt);
    beatIndexRef.current = startAt;
    setVisible(1);

    let cancelled = false;
    pausedRef.current = suspendedRef.current;
    setPaused(suspendedRef.current);

    const dead = () => cancelled || generation.current !== token;

    /** Sleep that does not count time spent paused, and ends if the user skips. */
    function waitPausable(ms: number, skipAt: number): Promise<void> {
      return new Promise((resolve) => {
        let remaining = ms;
        let last = performance.now();
        const tick = () => {
          if (dead() || skipRef.current !== skipAt) return resolve();
          const now = performance.now();
          if (!pausedRef.current) remaining -= now - last;
          last = now;
          if (remaining <= 0) return resolve();
          const id = window.setTimeout(tick, 60);
          timers.current.push(id);
        };
        const id = window.setTimeout(tick, 60);
        timers.current.push(id);
      });
    }

    async function run() {
      const tracks: Promise<HTMLAudioElement | null>[] = voice
        ? startNarration(beats.map((beat) => beat.narration), lang)
        : beats.map(() => Promise.resolve(null));

      function paintBeat(i: number, holdMs: number) {
        const beat = beats[i];
        const paintSkip = skipRef.current;
        beatIndexRef.current = i;
        setBeatIndex(i);
        setVisible(beat.on_screen.length ? 1 : 0);
        const delays = beat.on_screen.map((_item, index) =>
          index === 0 ? 0 : Math.min(110 + (index - 1) * 70, Math.max(0, holdMs - 80)),
        );
        setItemBudgets(delays.map((delay) => Math.max(400, holdMs - delay)));
        beat.on_screen.forEach((_item, index) => {
          if (index === 0) return;
          void waitPausable(delays[index], paintSkip).then(() => {
            if (dead() || skipRef.current !== paintSkip) return;
            setVisible((prev) => Math.max(prev, index + 1));
          });
        });
      }

      let i = startAt;
      let primed = false;
      while (i < beats.length) {
        if (dead()) return;
        if (seekRef.current != null) {
          i = seekRef.current;
          seekRef.current = null;
        }
        const skipAt = skipRef.current;
        const beat = beats[i];
        const plannedMs = plannedDurationMs(beat.narration);
        if (!primed) {
          paintBeat(i, holdDurationMs(null, plannedMs));
          primed = true;
        }
        const pending = await tracks[i].catch(() => null);
        // Hold the current frame until this beat's voice can actually play and
        // its real length is known. Timing from a word-count guess is what let
        // pictures run ahead of (or behind) the narration.
        if (pending) await audioReady(pending);
        // Only a clip that actually failed is skipped. One that has not
        // buffered yet still plays — play() is what makes Safari fetch it.
        const audio = pending && !pending.error ? pending : null;
        if (dead()) return;
        if (skipRef.current !== skipAt) continue;
        const audioMs = audioDurationMs(audio);
        const holdMs = holdDurationMs(audioMs, plannedMs);
        paintBeat(i, holdMs);
        setTiming({
          sceneId: beat.scene_id,
          index: i,
          total: beats.length,
          plannedMs,
          audioMs,
          holdMs,
          elapsedMs: null,
        });

        const previous = audioRef.current;
        if (previous && previous !== audio) previous.pause();
        attachNarration(audio);
        audioRef.current = audio;
        const started = performance.now();
        if (audio) {
          if (audio.currentTime > 0.05) audio.currentTime = 0;
          await new Promise<void>((resolve) => {
            let settled = false;
            const finish = () => {
              if (settled) return;
              settled = true;
              audio.removeEventListener("playing", arm);
              resolve();
            };
            // The safety net only starts counting once sound is really coming
            // out, so a slow download can never cut a line short.
            const arm = () => {
              const real = Number.isFinite(audio.duration) && audio.duration > 0
                ? Math.round(audio.duration * 1000)
                : null;
              void waitPausable((real ?? audioMs ?? plannedMs) + 1500, skipAt).then(finish);
            };
            // If sound never starts (dead link, stalled download), move on
            // after a generous wait instead of freezing on this beat.
            void waitPausable(plannedMs + 15000, skipAt).then(() => {
              if (audio.paused && !audio.ended && audio.currentTime === 0) finish();
            });
            audio.onended = finish;
            audio.onerror = finish;
            audio.addEventListener("playing", arm);
            if (!pausedRef.current) {
              void audio.play().catch(() => {
                unlockPlayback();
                void audio.play().catch(() => {
                  pausedRef.current = true;
                  setPaused(true);
                });
              });
            }
          });
          if (dead()) return;
          if (skipRef.current !== skipAt) continue;
          await waitPausable(HOLD_BUFFER_MS, skipAt);
        } else {
          await waitPausable(holdMs, skipAt);
        }
        if (dead()) return;
        if (skipRef.current !== skipAt) continue;
        setTiming((prev) =>
          prev && prev.sceneId === beat.scene_id
            ? { ...prev, elapsedMs: Math.round(performance.now() - started) }
            : prev,
        );
        i += 1;
      }
      if (dead()) return;
      audioRef.current = null;
      setPhase("done");
      phaseRef.current = "done";
      setVisible(99);
    }

    void run();
    return () => {
      cancelled = true;
      timers.current.forEach((id) => window.clearTimeout(id));
      stopDictation();
    };
    // `resolved` is re-created only when the plan or language changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, voice, lang, playId, session, resolved, videoSrc]);

  // Stored-video playback: restart on every new play request.
  useEffect(() => {
    const film = storedRef.current;
    if (!film || !videoSrc) return;
    film.currentTime = 0;
    setPhase("playing");
    phaseRef.current = "playing";
    if (suspendedRef.current) {
      film.pause();
      setPaused(true);
      return;
    }
    film.muted = !voice;
    setNeedsSound(false);
    void film.play().catch(() => {
      // Autoplay with sound was blocked (Safari does this unless the play
      // comes straight from a tap). Start silently and offer the sound.
      if (!voice) {
        setPaused(true);
        return;
      }
      film.muted = true;
      void film
        .play()
        .then(() => setNeedsSound(true))
        .catch(() => setPaused(true));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoSrc, playId, session]);

  useEffect(() => {
    const film = storedRef.current;
    if (!film || !videoSrc) return;
    film.muted = !voice || needsSound;
  }, [voice, videoSrc, needsSound]);

  const beats = plan ? flatten(plan) : [];
  const beat = beats[Math.min(beatIndex, Math.max(0, beats.length - 1))];
  const videoUrl = beat?.video?.url || "";
  const nextVideoUrl = beats[beatIndex + 1]?.video?.url || "";
  const imageUrl = beat?.image?.url || "";
  const isPhoto = Boolean(videoUrl || imageUrl || beat?.media === "sora" || beat?.media === "image");
  const overlayItems = beat
    ? beat.on_screen.filter((item) => !(isPhoto && item.type === "svg"))
    : [];
  const shown = overlayItems.filter((item, index) => index < visible || item.type === "svg");
  const bg = beat?.bg || "#F5F5F5";
  const hasDiagram = Boolean(beat?.on_screen.some((item) => item.type === "svg")) && !isPhoto;
  const stageClass = [
    "reel-stage",
    hasDiagram || isPhoto ? "" : "is-hero",
    isPhoto ? "is-photo" : "",
    `is-${beat?.visual?.emphasis || "normal"}`,
  ]
    .filter(Boolean)
    .join(" ");

  useEffect(() => {
    if (suspended) storedRef.current?.pause();
    if (suspended && phaseRef.current === "playing") setPausedState(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suspended]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !videoUrl) return;
    if (paused || phase !== "playing") {
      el.pause();
      return;
    }
    void el.play().catch(() => undefined);
  }, [paused, phase, videoUrl, playId, session]);

  return (
    <div className={`phone-stage${bare ? " is-bare" : ""}${actions && !bare ? " has-actions" : ""}`}>
      <div className="phone-with-controls">
        <div className="phone">
          {bare ? null : <PhoneChrome />}
          <div
            className={`phone-screen reel is-${beat?.purpose || "idle"} is-${phase}${paused ? " is-held" : ""}${plan ? " is-live" : ""}${isPhoto ? " has-photo" : ""}`}
            style={{ background: videoSrc ? "#0b0b0b" : bg }}
            role={plan ? "button" : undefined}
            tabIndex={plan ? 0 : undefined}
            aria-label={plan ? (paused || phase !== "playing" ? "Play reel" : "Pause reel") : undefined}
            onClick={onScreenToggle}
            onKeyDown={(event) => {
              if (!plan) return;
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onScreenToggle();
              }
            }}
          >
            {!plan ? (
              placeholder ?? <p className="reel-empty">Ask. The hook hits first.</p>
            ) : videoSrc ? (
              <>
                <video
                  key={videoSrc}
                  ref={storedRef}
                  className="reel-stored"
                  src={videoSrc}
                  playsInline
                  preload="auto"
                  onPlay={() => {
                    pausedRef.current = false;
                    setPaused(false);
                  }}
                  onPause={(event) => {
                    if (event.currentTarget.ended) return;
                    pausedRef.current = true;
                    setPaused(true);
                  }}
                  onEnded={() => {
                    setPhase("done");
                    phaseRef.current = "done";
                  }}
                />
                {needsSound ? <div className="reel-sound">Tap for sound</div> : null}
                {phase === "playing" && paused ? (
                  <div className="reel-paused" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor" /></svg>
                  </div>
                ) : phase === "done" ? (
                  <div className="reel-paused is-replay" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="M12 5a7 7 0 1 1-6.6 4.7M5 4v5h5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </div>
                ) : null}
              </>
            ) : (
              <>
                {nextVideoUrl ? (
                  <video src={nextVideoUrl} preload="auto" muted playsInline hidden />
                ) : null}
                {videoUrl ? (
                  <video
                    key={videoUrl}
                    ref={videoRef}
                    className="reel-video"
                    src={videoUrl}
                    muted
                    loop
                    playsInline
                    preload="auto"
                  />
                ) : imageUrl ? (
                  <img className="reel-still" src={imageUrl} alt="" />
                ) : null}
                {bare ? null : <div className="phone-island-space" />}
                <div className={stageClass}>
                  {shown.map((item, index) => (
                    <StageItem
                      key={`${beat.scene_id}-${index}-${item.type}`}
                      item={item}
                      isFirst={index === 0}
                      budgetMs={itemBudgets[index] ?? 900}
                    />
                  ))}
                </div>
                {bare ? null : <div className="phone-home-space" />}
                {phase === "playing" && paused ? (
                  <div className="reel-paused" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor" /></svg>
                  </div>
                ) : phase === "done" ? (
                  <div className="reel-paused is-replay" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="M12 5a7 7 0 1 1-6.6 4.7M5 4v5h5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </div>
                ) : null}
              </>
            )}
          </div>
          {import.meta.env.DEV && plan && !bare ? (
            <pre className="reel-debug">{`${
              timing
                ? `beat ${timing.index + 1}/${timing.total}  ${timing.sceneId}
planned  ${timing.plannedMs}ms  (word estimate)
audio    ${timing.audioMs == null ? "—" : `${timing.audioMs}ms`}
hold     ${timing.holdMs}ms  (speech+${HOLD_BUFFER_MS}ms)
elapsed  ${timing.elapsedMs == null ? "…" : `${timing.elapsedMs}ms`}`
                : "beat  …"
            }
script   ${plan.script_model || "—  (not recorded)"}
svg      ${plan.svg_model || "—  (not recorded)"}
media    ${beat?.media || "svg"}${videoUrl ? `  sora ${beat?.video?.seconds || ""}s` : ""}${imageUrl ? "  still" : ""}
visual   ${beat?.visual?.type || "—"}  ${beat?.visual?.motion || ""}
keep     ${beat?.visual?.keeps || "—"}`}</pre>
          ) : null}
        </div>
        {actions && !bare ? <div className="phone-actions">{actions}</div> : null}
      </div>
    </div>
  );
}
