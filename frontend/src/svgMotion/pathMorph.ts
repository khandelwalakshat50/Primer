/** Interpolate SVG path `d` only when command structure matches.
 *  Incompatible paths return null — the player must fall back, never throw. */

export type PathSeg = { c: string; n: number[] };

const CMD = /([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)/g;

export function parsePath(d: string): PathSeg[] | null {
  const segs: PathSeg[] = [];
  const src = (d || "").trim();
  if (!src) return null;
  let matched = false;
  src.replace(CMD, (_all, c: string, rest: string) => {
    matched = true;
    const n = rest
      .trim()
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(Number);
    if (n.some((x) => !Number.isFinite(x))) return "";
    segs.push({ c, n });
    return "";
  });
  return matched && segs.length ? segs : null;
}

export function pathsCompatible(a: PathSeg[], b: PathSeg[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i].c.toUpperCase() !== b[i].c.toUpperCase()) return false;
    if (a[i].n.length !== b[i].n.length) return false;
  }
  return true;
}

export function lerpPath(a: PathSeg[], b: PathSeg[], t: number): string {
  const u = Math.max(0, Math.min(1, t));
  return a
    .map((seg, i) => {
      const other = b[i];
      const nums = seg.n.map((n, j) => n + (other.n[j] - n) * u);
      const body = nums.map((n) => Number(n.toFixed(2))).join(" ");
      return body ? `${seg.c}${body}` : seg.c;
    })
    .join("");
}

export function interpolatePathD(from: string, to: string, t: number): string | null {
  const a = parsePath(from);
  const b = parsePath(to);
  if (!a || !b || !pathsCompatible(a, b)) return null;
  return lerpPath(a, b, t);
}

const NUMERIC_ATTRS = [
  "x",
  "y",
  "x1",
  "y1",
  "x2",
  "y2",
  "cx",
  "cy",
  "r",
  "rx",
  "ry",
  "width",
  "height",
] as const;

export function lerpNumberAttrs(from: Element, to: Element, t: number): boolean {
  if (from.tagName !== to.tagName) return false;
  let any = false;
  for (const attr of NUMERIC_ATTRS) {
    const a = from.getAttribute(attr);
    const b = to.getAttribute(attr);
    if (a == null || b == null) continue;
    const na = Number(a);
    const nb = Number(b);
    if (!Number.isFinite(na) || !Number.isFinite(nb)) continue;
    from.setAttribute(attr, String(na + (nb - na) * t));
    any = true;
  }
  if (from.tagName.toLowerCase() === "path") {
    const d = interpolatePathD(from.getAttribute("d") || "", to.getAttribute("d") || "", t);
    if (d) {
      from.setAttribute("d", d);
      any = true;
    } else if ((from.getAttribute("d") || "") !== (to.getAttribute("d") || "")) {
      return false;
    }
  }
  return any;
}

export function shapesMorphable(from: Element, to: Element): boolean {
  if (from.tagName !== to.tagName) return false;
  if (from.tagName.toLowerCase() === "path") {
    const a = parsePath(from.getAttribute("d") || "");
    const b = parsePath(to.getAttribute("d") || "");
    return Boolean(a && b && pathsCompatible(a, b));
  }
  return NUMERIC_ATTRS.some((attr) => from.hasAttribute(attr) && to.hasAttribute(attr));
}
