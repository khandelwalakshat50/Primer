/** Stable identities for g.r-el groups. Prefer authored ids; never invent
 *  shared ids for unrelated shapes. */

export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48);
}

export function shapeSignature(group: Element): string {
  const tags = Array.from(group.querySelectorAll("path,rect,circle,ellipse,line,polyline,polygon,text"))
    .map((el) => el.tagName.toLowerCase())
    .join(",");
  return tags || group.tagName.toLowerCase();
}

export function groupObjectId(group: Element, index: number): string {
  const stamped = group.getAttribute("data-oid");
  if (stamped) return stamped;
  const explicit =
    group.getAttribute("id") ||
    group.getAttribute("data-id") ||
    group.getAttribute("data-keep") ||
    group.getAttribute("data-object");
  if (explicit?.trim()) return slug(explicit);
  const label = (group.textContent || "").replace(/\s+/g, " ").trim();
  if (label.length >= 1 && label.length <= 32) return `t:${slug(label)}`;
  return `i:${index}:${shapeSignature(group)}`;
}

export function stampObjectIds(groups: SVGGElement[]): Map<string, SVGGElement> {
  const used = new Map<string, number>();
  const byId = new Map<string, SVGGElement>();
  groups.forEach((g, i) => {
    let id = groupObjectId(g, i);
    const n = (used.get(id) || 0) + 1;
    used.set(id, n);
    if (n > 1) {
      if (import.meta.env.DEV) {
        console.debug("[primer-motion] duplicate object id", id, "→", `${id}__${n}`);
      }
      id = `${id}__${n}`;
    }
    g.setAttribute("data-oid", id);
    byId.set(id, g);
  });
  return byId;
}

export function revealGroups(svg: SVGSVGElement): SVGGElement[] {
  return Array.from(svg.querySelectorAll<SVGGElement>("g.r-el"));
}
