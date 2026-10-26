/** GPU-friendly WAAPI helpers. Pause-aware so the phone screen freeze still works. */

export const EASE_SETTLE = "cubic-bezier(0.22, 1, 0.36, 1)";

export type AnimHandle = {
  pause: () => void;
  play: () => void;
  cancel: () => void;
};

export function waapi(
  el: Element,
  keyframes: Keyframe[] | PropertyIndexedKeyframes,
  opts: KeyframeAnimationOptions,
): Animation {
  return el.animate(keyframes, {
    easing: EASE_SETTLE,
    fill: "forwards",
    ...opts,
  });
}

export function pauseAnimations(root: Element, paused: boolean) {
  const list = root.getAnimations({ subtree: true });
  for (const anim of list) {
    if (paused) anim.pause();
    else if (anim.playState === "paused") anim.play();
  }
}

export function bbox(el: Element): DOMRect {
  try {
    return (el as SVGGraphicsElement).getBBox();
  } catch {
    return new DOMRect(0, 0, 0, 0);
  }
}

export function originFromBox(box: DOMRect): string {
  return `${box.x + box.width / 2}px ${box.y + box.height / 2}px`;
}

export function runRaf(
  duration: number,
  draw: (t: number) => void,
  isPaused: () => boolean,
  isDead: () => boolean,
): Promise<void> {
  return new Promise((resolve) => {
    let elapsed = 0;
    let last = performance.now();
    const tick = (now: number) => {
      if (isDead()) return resolve();
      const dt = now - last;
      last = now;
      if (!isPaused()) elapsed += dt;
      const t = duration <= 0 ? 1 : Math.min(1, elapsed / duration);
      draw(t);
      if (t >= 1) return resolve();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

export function settleEase(t: number): number {
  // cubic-bezier(0.22, 1, 0.36, 1) approximation
  const a = 1 - t;
  return 1 - a * a * a * (1 - t * 0.22);
}
