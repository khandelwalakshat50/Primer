/** Breath after TTS ends. Not a minimum beat length. */
export const HOLD_BUFFER_MS = 500;

export function plannedDurationMs(narration: string): number {
  const words = narration.trim().split(/\s+/).filter(Boolean).length;
  return Math.round(words * 420);
}

export function audioDurationMs(audio: HTMLAudioElement | null): number | null {
  if (!audio) return null;
  if (!Number.isFinite(audio.duration) || audio.duration <= 0) return null;
  return Math.round(audio.duration * 1000);
}

export function holdDurationMs(audioMs: number | null, plannedMs: number): number {
  const speechMs = audioMs ?? plannedMs;
  return speechMs + HOLD_BUFFER_MS;
}

export type BeatTiming = {
  sceneId: string;
  index: number;
  total: number;
  plannedMs: number;
  audioMs: number | null;
  holdMs: number;
  elapsedMs: number | null;
};
