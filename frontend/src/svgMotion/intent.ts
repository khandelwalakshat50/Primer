import type { BeatVisual } from "../types";

/** Player-side motion kinds. Director describes intent; we pick the execution. */
export type MotionKind = "rise" | "peel" | "travel" | "grow" | "draw";

export type MotionIntent = {
  enter: MotionKind;
  emphasize: "none" | "pulse";
  exit: "fade" | "fade-down";
};

const TYPE_ENTER: Record<string, MotionKind> = {
  accumulation: "rise",
  stack: "rise",
  split: "peel",
  comparison: "peel",
  before_after: "peel",
  flow: "travel",
  sequence: "travel",
  timeline: "travel",
  network: "travel",
  transformation: "grow",
  grow: "grow",
  merge: "grow",
  counter: "grow",
};

function kindFromPhrase(raw: string): MotionKind | null {
  const t = raw.toLowerCase();
  if (!t) return null;
  if (/\b(rise|stack|accumul|build(?:ing)? up|upward)\b/.test(t)) return "rise";
  if (/\b(peel|split|apart|separat|left and right|two sides)\b/.test(t)) return "peel";
  if (/\b(travel|flow|along|path|pipeline|arrow|particle|money moving)\b/.test(t)) return "travel";
  if (/\b(grow|scale|larger|taller|expand|compound)\b/.test(t)) return "grow";
  if (/\b(draw|fade|reveal)\b/.test(t)) return "draw";
  return null;
}

function kindFromAttr(el: Element | null): MotionKind | null {
  if (!el) return null;
  const raw = (
    el.getAttribute("data-motion") ||
    el.getAttribute("data-enter") ||
    ""
  )
    .trim()
    .toLowerCase()
    .replace(/-/g, "_");
  if (raw === "rise" || raw === "stack") return "rise";
  if (raw === "peel" || raw === "split") return "peel";
  if (raw === "travel" || raw === "flow") return "travel";
  if (raw === "grow" || raw === "scale") return "grow";
  if (raw === "draw" || raw === "fade") return "draw";
  return kindFromPhrase(raw);
}

export function resolveIntent(visual: BeatVisual | undefined, group?: Element | null): MotionIntent {
  const fromGroup = kindFromAttr(group ?? null);
  const fromType = visual?.type ? TYPE_ENTER[visual.type] : undefined;
  const fromMotion = kindFromPhrase(visual?.motion || "");
  const fromChanges = kindFromPhrase(visual?.changes || "");
  const enter = fromGroup || fromType || fromMotion || fromChanges || "draw";
  const emphasize: MotionIntent["emphasize"] =
    group?.getAttribute("data-emphasize") === "pulse" || visual?.emphasis === "loud"
      ? "pulse"
      : "none";
  const exitAttr = (group?.getAttribute("data-exit") || "").toLowerCase();
  const exit: MotionIntent["exit"] = exitAttr === "fade-down" || enter === "rise" ? "fade-down" : "fade";
  return { enter, emphasize, exit };
}

export function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
}
