import { useEffect, useRef, useState, type TouchEvent } from "react";
import { unlockPlayback } from "./api";

const QUERY = "(max-width: 860px)";

export function useWatchMode(): boolean {
  const [watch, setWatch] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia(QUERY).matches : false,
  );

  useEffect(() => {
    const media = window.matchMedia(QUERY);
    const sync = () => setWatch(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  return watch;
}

export function useLessonSwipe(onNext: () => void, onPrev: () => void) {
  const origin = useRef<{ x: number; y: number } | null>(null);

  return {
    onTouchStart(event: TouchEvent) {
      unlockPlayback();
      const touch = event.changedTouches[0];
      origin.current = { x: touch.clientX, y: touch.clientY };
    },
    onTouchEnd(event: TouchEvent) {
      const start = origin.current;
      origin.current = null;
      if (!start) return;
      const touch = event.changedTouches[0];
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.15) return;
      if (dx < 0) onNext();
      else onPrev();
    },
  };
}
