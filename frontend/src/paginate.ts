export type PageMetrics = {
  width: number;
  maxHeight: number;
};

export const FALLBACK_METRICS: PageMetrics = {
  width: 620,
  maxHeight: 720,
};

function renderInto(el: HTMLElement, text: string) {
  el.replaceChildren();
  const parts = text.split(/\n+/).filter((p) => p.length > 0);
  for (const part of parts) {
    const p = document.createElement("p");
    p.style.margin = "0";
    p.textContent = part;
    el.appendChild(p);
  }
}

function measureBox(
  width: number,
  family: string,
  size: string,
): HTMLElement {
  const el = document.createElement("div");
  el.style.cssText = [
    "position:absolute",
    "left:-9999px",
    "top:0",
    `width:${Math.max(120, width)}px`,
    `font-family:${family}`,
    `font-size:${size}`,
    "font-weight:400",
    "letter-spacing:0.01em",
    "line-height:28px",
    "visibility:hidden",
    "pointer-events:none",
  ].join(";");
  document.body.appendChild(el);
  return el;
}

function lastBreak(text: string, index: number): number {
  if (index >= text.length) return text.length;
  const slice = text.slice(0, index);
  const para = slice.lastIndexOf("\n\n");
  if (para >= Math.min(40, index * 0.4)) return para;
  const sentence = Math.max(slice.lastIndexOf(". "), slice.lastIndexOf("? "));
  if (sentence >= Math.min(40, index * 0.4)) return sentence + 1;
  const space = slice.lastIndexOf(" ");
  if (space > 0) return space;
  return index;
}

export function splitToFit(
  text: string,
  metrics: PageMetrics,
  family = "Kalam, cursive",
  size = "1.4rem",
): { fitted: string; rest: string } {
  const trimmed = text.trim();
  if (!trimmed) return { fitted: "", rest: "" };

  const box = measureBox(metrics.width, family, size);
  renderInto(box, trimmed);
  if (box.offsetHeight <= metrics.maxHeight) {
    box.remove();
    return { fitted: trimmed, rest: "" };
  }

  let lo = 0;
  let hi = trimmed.length;
  let fit = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    renderInto(box, trimmed.slice(0, mid));
    if (box.offsetHeight <= metrics.maxHeight) {
      fit = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  box.remove();

  const cut = lastBreak(trimmed, fit);
  const at = cut > 0 ? cut : fit;
  return {
    fitted: trimmed.slice(0, at).trim(),
    rest: trimmed.slice(at).trim(),
  };
}

export function paginateText(
  text: string,
  metrics: PageMetrics,
  family = "Kalam, cursive",
  size = "1.4rem",
): string[] {
  const pages: string[] = [];
  let remaining = text.trim();
  let guard = 0;
  while (remaining && guard < 12) {
    const { fitted, rest } = splitToFit(remaining, metrics, family, size);
    if (!fitted) {
      pages.push(remaining);
      break;
    }
    pages.push(fitted);
    remaining = rest;
    guard += 1;
  }
  return pages.length ? pages : [""];
}
