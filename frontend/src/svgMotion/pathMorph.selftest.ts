import { interpolatePathD, parsePath, pathsCompatible } from "./pathMorph.ts";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const a = parsePath("M0 0 L10 0 L10 10 Z");
const b = parsePath("M0 0 L20 0 L20 20 Z");
assert(a && b && pathsCompatible(a, b), "compatible rectangles");
const mid = interpolatePathD("M0 0 L10 0 L10 10 Z", "M0 0 L20 0 L20 20 Z", 0.5);
assert(mid?.includes("L15"), `lerp got ${mid}`);

const c = parsePath("M0 0 C1 2 3 4 5 6");
const d = parsePath("M0 0 L10 10");
assert(c && d && !pathsCompatible(c, d), "incompatible commands");
assert(interpolatePathD("M0 0 C1 2 3 4 5 6", "M0 0 L10 10", 0.5) === null, "fallback null");

console.log("pathMorph selftest ok");
