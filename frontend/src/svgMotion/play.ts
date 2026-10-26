/**
 * Light CSS motion on existing g.r-el groups. No morph, no scale-from-tiny
 * (that looked like a loader and left the frame empty). Voiceover still owns timing.
 */
import type { BeatVisual } from "../types";

export const IDEAL_STROKE_MS = 520;
export const IDEAL_STAGGER_MS = 90;
export const IDEAL_ENTER_MS = 320;
const MIN_STROKE_MS = 220;
const MIN_STAGGER_MS = 50;
const MIN_ENTER_MS = 200;

const SHAPE_SEL = "path, line, polyline, polygon, circle, ellipse, rect";

export type PlayContext = {
  budgetMs: number;
  visual?: BeatVisual;
  beatIndex: number;
  isPaused: () => boolean;
  isDead: () => boolean;
  debug: boolean;
};

export type MotionDebugInfo = {
  beat: number;
  visualType: string;
  motion: string;
  groups: { id: string; enter: string; morph: boolean }[];
};

function isStrokeShape(el: Element): boolean {
  const stroke = el.getAttribute("stroke");
  const fill = el.getAttribute("fill");
  return Boolean(stroke) && stroke !== "none" && (!fill || fill === "none");
}

function groupKind(g: SVGGElement): "arrow" | "label" | "body" {
  const hasText = Boolean(g.querySelector("text"));
  const hasFill = Boolean(g.querySelector("rect, circle, ellipse, polygon"));
  const hasStroke = Array.from(g.querySelectorAll("line, polyline, path")).some(isStrokeShape);
  if (hasStroke && !hasFill) return "arrow";
  if (hasText && !hasFill) return "label";
  return "body";
}

export function playFresh(svg: SVGSVGElement, ctx: PlayContext): MotionDebugInfo {
  const groups = Array.from(svg.querySelectorAll<SVGGElement>("g.r-el"));
  const n = Math.max(1, groups.length);
  const ideal = (n - 1) * IDEAL_STAGGER_MS + IDEAL_ENTER_MS + 80;
  const available = Math.max(280, ctx.budgetMs - 40);
  const scale = ideal > available ? available / ideal : 1;
  const enterMs = Math.max(MIN_ENTER_MS, IDEAL_ENTER_MS * scale);
  const staggerMs = Math.max(MIN_STAGGER_MS, IDEAL_STAGGER_MS * scale);
  const strokeMs = Math.max(MIN_STROKE_MS, IDEAL_STROKE_MS * scale);

  groups.forEach((g, i) => {
    const delay = i * staggerMs;
    const kind = groupKind(g);
    g.setAttribute("data-motion", kind);
    const name = kind === "label" ? "label-pop" : "group-in";
    g.style.animation = `${name} ${enterMs}ms ease-out ${delay}ms both`;
    if (kind === "arrow" || kind === "body") {
      Array.from(g.querySelectorAll<SVGGraphicsElement>(SHAPE_SEL))
        .filter(isStrokeShape)
        .forEach((el) => {
          let length = 0;
          try {
            length = (el as unknown as SVGGeometryElement).getTotalLength();
          } catch {
            length = 0;
          }
          if (!length || !Number.isFinite(length)) return;
          el.style.strokeDasharray = `${length}`;
          el.style.strokeDashoffset = `${length}`;
          el.style.animation = `draw-on ${strokeMs}ms cubic-bezier(0.16, 1, 0.3, 1) ${delay}ms forwards`;
        });
    }
  });

  return {
    beat: ctx.beatIndex,
    visualType: ctx.visual?.type || "",
    motion: ctx.visual?.motion || "",
    groups: groups.map((g, i) => ({
      id: String(i),
      enter: groupKind(g),
      morph: false,
    })),
  };
}

export function pauseSvgMotion(_root: Element, _paused: boolean) {
  /* CSS animation-play-state on .is-held already freezes these. */
}

export function motionDebugEnabled(): boolean {
  return false;
}
