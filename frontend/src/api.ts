import type { Curriculum, ExportJob, ReelJob, ReelPlan, Turn } from "./types";

export type PrimerModels = {
  openai_model: string;
  gemini_model: string;
  claude_model?: string;
  director_model: string;
  director_gemini_model: string;
  deepgram_tts_model: string;
  has_openai_key: boolean;
  has_gemini_key: boolean;
  has_claude_key?: boolean;
  has_deepgram_key: boolean;
  has_elevenlabs_key?: boolean;
  has_sora_key?: boolean;
  sora_model?: string;
};

export type LlmProvider = "auto" | "openai" | "gemini" | "claude";
export type VoiceLang = "en" | "hi";

export async function fetchPrimerModels(): Promise<PrimerModels | null> {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

async function failure(res: Response, fallback: string): Promise<Error> {
  let detail = `${fallback} (${res.status})`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* ignore */
  }
  const err = new Error(detail);
  (err as Error & { status?: number }).status = res.status;
  return err;
}

async function postJson<T>(url: string, body?: unknown, fallback = "Request failed"): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await failure(res, fallback);
  return res.json();
}

/** Start a reel on the server. It keeps going if this tab closes. */
export function createReelJob(payload: {
  messages: Turn[];
  notebook: string;
  current_topic: string;
  visual_engine?: "svg" | "sora";
  provider?: LlmProvider;
}): Promise<ReelJob> {
  return postJson("/api/reels", payload, "Could not start the reel");
}

/** Resolves null when the server has no such job (an interrupted legacy reel). */
export async function fetchReelJob(jobId: string): Promise<ReelJob | null> {
  const res = await fetch(`/api/reels/${encodeURIComponent(jobId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw await failure(res, "Could not check the reel");
  return res.json();
}

export function stopReelJob(jobId: string): Promise<ReelJob> {
  return postJson(`/api/reels/${encodeURIComponent(jobId)}/stop`, undefined, "Stop failed");
}

export function retryReelJob(jobId: string): Promise<ReelJob> {
  return postJson(`/api/reels/${encodeURIComponent(jobId)}/retry`, undefined, "Retry failed");
}

/** Retry every failed or stopped lesson of a course; finished ones are kept. */
export function resumeCurriculum(curriculumId: string): Promise<Curriculum> {
  return postJson(
    `/api/curriculum/${encodeURIComponent(curriculumId)}/resume`,
    undefined,
    "Could not resume the course",
  );
}

export function startExport(payload: {
  title: string;
  reels: ReelPlan[];
  lang: VoiceLang;
}): Promise<ExportJob> {
  return postJson("/api/exports", payload, "Could not start the video export");
}

/** The stored (or in-progress) video for these reels; null if none exists. */
export async function lookupExport(payload: {
  reels: ReelPlan[];
  lang: VoiceLang;
  title: string;
  prepare: boolean;
}): Promise<ExportJob | null> {
  const found = await postJson<ExportJob | { status: "none" }>(
    "/api/exports/lookup",
    payload,
    "Could not check for a saved video",
  );
  return found.status === "none" ? null : (found as ExportJob);
}

export async function fetchExport(exportId: string): Promise<ExportJob> {
  const res = await fetch(`/api/exports/${encodeURIComponent(exportId)}`);
  if (!res.ok) throw await failure(res, "Could not check the export");
  return res.json();
}

export function exportFileUrl(exportId: string, filename: string): string {
  return `/api/exports/${encodeURIComponent(exportId)}/file?name=${encodeURIComponent(filename)}`;
}

type CurriculumStreamHandlers = {
  onPlan?: (curriculum: Curriculum) => void;
  onReelReady?: (curriculum: Curriculum) => void;
  onProgress?: (label: string) => void;
};

type InFlight = {
  promise: Promise<Curriculum>;
  handlers: CurriculumStreamHandlers[];
  latest: Curriculum | null;
  abort: AbortController;
};

const curriculumInFlight = new Map<string, InFlight>();

function sourceLock(text: string): string {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `src:${(hash >>> 0).toString(16)}`;
}

export function curriculumLock(topic: string, sourceText = ""): string {
  const src = sourceText.trim();
  if (src) return sourceLock(src);
  return topic.trim().toLowerCase().replace(/\s+/g, " ");
}

function courseStillOpen(c: Curriculum | null | undefined): boolean {
  if (!c) return false;
  if (c.status === "planning" || c.status === "generating") return true;
  return c.reels.some((r) => r.status !== "ready" && r.status !== "failed");
}

export function abortCurriculumTopic(topic: string, sourceText = ""): void {
  const lock = curriculumLock(topic, sourceText);
  const entry = curriculumInFlight.get(lock);
  entry?.abort.abort();
  curriculumInFlight.delete(lock);
}

export async function stopCurriculum(curriculumId: string): Promise<Curriculum> {
  const res = await fetch(`/api/curriculum/${encodeURIComponent(curriculumId)}/stop`, {
    method: "POST",
  });
  if (!res.ok) {
    let detail = `Stop failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function stopCurriculumByTopic(topic: string): Promise<Curriculum> {
  const res = await fetch("/api/curricula/stop-open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic }),
  });
  if (!res.ok) {
    let detail = `Stop failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function createCurriculum(
  topic: string,
  handlers: CurriculumStreamHandlers = {},
  visualEngine: "svg" | "sora" = "svg",
  provider: LlmProvider = "auto",
  sourceText = "",
): Promise<Curriculum> {
  const lock = curriculumLock(topic, sourceText);
  const existing = curriculumInFlight.get(lock);
  if (existing) {
    if (courseStillOpen(existing.latest) || !existing.latest) {
      existing.handlers.push(handlers);
      if (existing.latest) {
        handlers.onPlan?.(existing.latest);
      }
      return existing.promise;
    }
    curriculumInFlight.delete(lock);
  }

  const entry: InFlight = {
    promise: Promise.resolve({} as Curriculum),
    handlers: [handlers],
    latest: null,
    abort: new AbortController(),
  };
  const promise = startCurriculum(topic, visualEngine, provider, entry, sourceText);
  entry.promise = promise;
  curriculumInFlight.set(lock, entry);
  try {
    return await promise;
  } finally {
    const latest = curriculumInFlight.get(lock)?.latest;
    if (!courseStillOpen(latest)) {
      curriculumInFlight.delete(lock);
    }
  }
}

async function startCurriculum(
  topic: string,
  visualEngine: string,
  provider: LlmProvider,
  entry: InFlight,
  sourceText = "",
): Promise<Curriculum> {
  const emitPlan = (c: Curriculum) => {
    entry.latest = c;
    for (const h of entry.handlers) h.onPlan?.(c);
  };
  const emitReady = (c: Curriculum) => {
    entry.latest = c;
    for (const h of entry.handlers) h.onReelReady?.(c);
  };
  const emitProgress = (label: string) => {
    for (const h of entry.handlers) h.onProgress?.(label);
  };

  const res = await fetch("/api/curriculum", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      topic,
      source_text: sourceText || undefined,
      visual_engine: visualEngine,
      provider,
    }),
    signal: entry.abort.signal,
  });

  if (!res.ok || !res.body) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let curriculum: Curriculum | null = null;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part
          .split("\n")
          .filter((row) => row.startsWith("data:"))
          .map((row) => row.slice(5).trim())
          .join("");
        if (!line) continue;
        let data: { event?: string; detail?: string; curriculum?: Curriculum; label?: string };
        try {
          data = JSON.parse(line);
        } catch {
          continue;
        }
        if (data.event === "error") {
          throw new Error(data.detail || "Request failed.");
        } else if (data.event === "plan" && data.curriculum) {
          curriculum = data.curriculum;
          emitPlan(data.curriculum);
        } else if (data.event === "reel_ready" && data.curriculum) {
          curriculum = data.curriculum;
          emitReady(data.curriculum);
        } else if (data.event === "progress" && data.label) {
          emitProgress(data.label);
        }
      }
    }
  } catch (err) {
    if (entry.abort.signal.aborted && (curriculum || entry.latest)) {
      return (curriculum || entry.latest) as Curriculum;
    }
    throw err;
  }

  if (!curriculum) {
    throw new Error("No curriculum returned.");
  }
  return curriculum;
}

export async function fetchCurriculum(
  curriculumId: string,
  opts: { resume?: boolean } = {},
): Promise<Curriculum> {
  const q = opts.resume ? "?resume=1" : "";
  const res = await fetch(`/api/curriculum/${curriculumId}${q}`);
  if (!res.ok) {
    throw new Error(`Request failed (${res.status})`);
  }
  return res.json();
}

export async function fetchCurricula(): Promise<Curriculum[]> {
  const res = await fetch("/api/curricula");
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data.curricula) ? data.curricula : [];
}

let activeAudio: HTMLAudioElement | null = null;
let fetching = 0;

export function stopDictation() {
  fetching += 1;
  if (activeAudio) {
    activeAudio.pause();
    activeAudio = null;
  }
}

/** In-memory clip URLs and decoded Audio nodes. Durable copies live on disk
 *  (backend/data/audio). GET /api/speak is cacheable by the browser; POST is
 *  only used when the narration is too long for a query string. */
const clipUrls = new Map<string, string>();
const audioEls = new Map<string, HTMLAudioElement>();
const inFlight = new Map<string, Promise<string | null>>();
const MAX_CACHED_CLIPS = 240;
const GET_URL_LIMIT = 1600;

function clipKey(text: string, lang: VoiceLang): string {
  return `${lang}:${text}`;
}

function speakGetUrl(text: string, lang: VoiceLang): string | null {
  const params = new URLSearchParams({ text: text.trim(), lang });
  const url = `/api/speak?${params.toString()}`;
  return url.length <= GET_URL_LIMIT ? url : null;
}

function rememberClip(key: string, url: string) {
  if (clipUrls.size >= MAX_CACHED_CLIPS) {
    const oldest = clipUrls.keys().next().value;
    if (oldest && oldest !== key) {
      clipUrls.delete(oldest);
      const old = audioEls.get(oldest);
      if (old) {
        old.pause();
        audioEls.delete(oldest);
      }
    }
  }
  clipUrls.set(key, url);
}

function audioForClip(key: string, url: string): HTMLAudioElement {
  const existing = audioEls.get(key);
  if (existing) return existing;
  const audio = new Audio();
  audio.preload = "auto";
  audio.playsInline = true;
  audio.src = url;
  audioEls.set(key, audio);
  audio.load();
  return audio;
}

let playbackUnlocked = false;

/** iOS will not buffer or play until a gesture has unlocked media. */
export function unlockPlayback(): void {
  if (playbackUnlocked) return;
  playbackUnlocked = true;
  const silent = new Audio();
  silent.muted = true;
  silent.playsInline = true;
  void silent.play().then(() => {
    silent.pause();
    silent.src = "";
  }).catch(() => {
    playbackUnlocked = false;
  });
  for (const audio of audioEls.values()) {
    if (audio.readyState < 2) audio.load();
  }
}

async function clipUrlFor(text: string, lang: VoiceLang = "en"): Promise<string | null> {
  const key = clipKey(text, lang);
  const cached = clipUrls.get(key);
  if (cached) {
    audioForClip(key, cached);
    return cached;
  }

  const pending = inFlight.get(key);
  if (pending) return pending;

  const getUrl = speakGetUrl(text, lang);
  if (getUrl) {
    rememberClip(key, getUrl);
    audioForClip(key, getUrl);
    return getUrl;
  }

  const task = (async () => {
    try {
      const res = await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim(), lang }),
      });
      if (!res.ok) return null;
      const blob = await res.blob();
      if (!blob.size) return null;
      const url = URL.createObjectURL(blob);
      rememberClip(key, url);
      audioForClip(key, url);
      return url;
    } catch {
      return null;
    } finally {
      inFlight.delete(key);
    }
  })();
  inFlight.set(key, task);
  return task;
}

function waitAudioReady(audio: HTMLAudioElement): Promise<void> {
  if (audio.readyState >= 2) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, 15000);
    const done = () => {
      window.clearTimeout(timer);
      resolve();
    };
    audio.addEventListener("canplay", done, { once: true });
    audio.addEventListener("error", done, { once: true });
  });
}

async function audioFromText(
  text: string,
  lang: VoiceLang = "en",
): Promise<HTMLAudioElement | null> {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const key = clipKey(trimmed, lang);
  const url = await clipUrlFor(trimmed, lang);
  if (!url) return null;
  const audio = audioForClip(key, url);
  return audio;
}

/**
 * Resolves once the clip knows its length (or errors, or `timeoutMs` passes).
 * Never required for playback: Safari and some Chrome setups fetch nothing
 * until play() is called, so callers must still play a clip that is not ready.
 */
export function audioReady(audio: HTMLAudioElement, timeoutMs = 2500): Promise<void> {
  const known = () => Number.isFinite(audio.duration) && audio.duration > 0;
  if (known() || audio.error) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      window.clearTimeout(timer);
      audio.removeEventListener("loadedmetadata", done);
      audio.removeEventListener("durationchange", check);
      audio.removeEventListener("error", done);
      resolve();
    };
    const check = () => {
      if (known()) done();
    };
    const timer = window.setTimeout(done, timeoutMs);
    audio.addEventListener("loadedmetadata", done);
    audio.addEventListener("durationchange", check);
    audio.addEventListener("error", done);
  });
}

export function warmNarration(lines: string[], lang: VoiceLang = "en"): void {
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    void clipUrlFor(trimmed, lang).then((url) => {
      if (!url) return;
      void waitAudioReady(audioForClip(clipKey(trimmed, lang), url));
    });
  }
}

export async function prefetchNarration(
  lines: string[],
  lang: VoiceLang = "en",
): Promise<void> {
  await Promise.all(lines.map((line) => clipUrlFor(line.trim(), lang)));
}

/**
 * Fire every TTS request at once but return the promises unawaited, so the
 * player can start on beat 0 as soon as ITS clip is ready instead of waiting
 * for the slowest clip in the reel.
 */
export function startNarration(
  lines: string[],
  lang: VoiceLang = "en",
): Promise<HTMLAudioElement | null>[] {
  return lines.map((line) => audioFromText(line, lang));
}

export async function speakNarration(
  text: string,
  lang: VoiceLang = "en",
): Promise<HTMLAudioElement | null> {
  const generation = fetching;
  const audio = await audioFromText(text, lang);
  if (generation !== fetching) {
    audio?.pause();
    return null;
  }
  activeAudio = audio;
  return audio;
}

export function attachNarration(audio: HTMLAudioElement | null) {
  if (audio) activeAudio = audio;
}
