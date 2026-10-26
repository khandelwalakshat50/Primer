from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

REEL_VIEWBOX = (0.0, 0.0, 360.0, 440.0)
REEL_MARGIN = 20.0
OVERFLOW_NOTE = (
    "Your last diagram overflowed the frame or had no viewBox. "
    "Use viewBox=\"0 0 360 440\" and keep ALL shapes and labels inside "
    "x:20-340, y:20-420. Do not set width/height in px without a viewBox."
)

_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", _NS)


def _strip_ns_prefixes(xml: str) -> str:
    xml = re.sub(r"\sxmlns:ns\d+=['\"][^'\"]*['\"]", "", xml)
    xml = re.sub(r"</ns\d+:", "</", xml)
    xml = re.sub(r"<ns\d+:", "<", xml)
    xml = re.sub(r"\sstroke-dasharray=['\"][^'\"]*['\"]", "", xml)
    xml = re.sub(r"\sstroke-dashoffset=['\"][^'\"]*['\"]", "", xml)
    return xml


def _serialize(root: ET.Element) -> str:
    ET.register_namespace("", _NS)
    return _strip_ns_prefixes(ET.tostring(root, encoding="unicode"))


def sanitize_svg_markup(svg: str) -> str:
    return _strip_ns_prefixes(svg or "")


_STYLE_ABS = re.compile(r"position\s*:\s*absolute", re.I)
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1].lower()


def parse_viewbox(svg: str) -> tuple[float, float, float, float] | None:
    match = re.search(r"viewBox\s*=\s*['\"]([^'\"]+)['\"]", svg, re.I)
    if not match:
        return None
    parts = [p for p in re.split(r"[\s,]+", match.group(1).strip()) if p]
    if len(parts) != 4:
        return None
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    except ValueError:
        return None


def rejects_absolute_layout(svg: str) -> bool:
    if _STYLE_ABS.search(svg):
        return True
    has_vb = parse_viewbox(svg) is not None
    width = re.search(r"<svg\b[^>]*\bwidth\s*=\s*['\"]([^'\"]+)['\"]", svg, re.I)
    height = re.search(r"<svg\b[^>]*\bheight\s*=\s*['\"]([^'\"]+)['\"]", svg, re.I)
    if has_vb:
        return False
    if width or height:
        return True
    return True


def _nums(text: str) -> list[float]:
    out = []
    for raw in _NUM.findall(text or ""):
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


_PATH_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)")


def path_points(d: str) -> list[tuple[float, float]]:
    """On-curve points (plus control points) of a path's d attribute.

    Reading the raw numbers out of `d` and pairing them up is wrong for
    relative commands: `M 80 155 l 10 -6` would register a phantom point at
    (10, -6), dragging the measured box toward the origin and pushing the
    whole drawing off-center. So actually walk the commands.

    Control points are included, which can overstate a curve's box — that
    errs toward scaling down slightly, never toward overflowing the frame.
    """
    tokens: list = []
    for match in _PATH_TOKEN.finditer(d or ""):
        if match.group(1):
            tokens.append(match.group(1))
        else:
            try:
                tokens.append(float(match.group(2)))
            except ValueError:
                continue

    pts: list[tuple[float, float]] = []
    i = 0
    cx = cy = sx = sy = 0.0
    cmd: str | None = None

    def take(n: int) -> list[float] | None:
        nonlocal i
        if i + n > len(tokens):
            return None
        chunk = tokens[i : i + n]
        if not all(isinstance(v, float) for v in chunk):
            return None
        i += n
        return chunk  # type: ignore[return-value]

    while i < len(tokens):
        token = tokens[i]
        if isinstance(token, str):
            i += 1
            if token in ("Z", "z"):
                cx, cy = sx, sy
                pts.append((cx, cy))
                cmd = None
            else:
                cmd = token
            continue
        if cmd is None:
            i += 1
            continue
        rel = cmd.islower()
        head = cmd.upper()
        if head == "M":
            v = take(2)
            if not v:
                break
            cx, cy = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
            sx, sy = cx, cy
            pts.append((cx, cy))
            cmd = "l" if rel else "L"
        elif head == "L":
            v = take(2)
            if not v:
                break
            cx, cy = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
            pts.append((cx, cy))
        elif head == "H":
            v = take(1)
            if not v:
                break
            cx = cx + v[0] if rel else v[0]
            pts.append((cx, cy))
        elif head == "V":
            v = take(1)
            if not v:
                break
            cy = cy + v[0] if rel else v[0]
            pts.append((cx, cy))
        elif head == "C":
            v = take(6)
            if not v:
                break
            for k in (0, 2, 4):
                pts.append((cx + v[k], cy + v[k + 1]) if rel else (v[k], v[k + 1]))
            cx, cy = (cx + v[4], cy + v[5]) if rel else (v[4], v[5])
        elif head in ("S", "Q"):
            v = take(4)
            if not v:
                break
            for k in (0, 2):
                pts.append((cx + v[k], cy + v[k + 1]) if rel else (v[k], v[k + 1]))
            cx, cy = (cx + v[2], cy + v[3]) if rel else (v[2], v[3])
        elif head == "T":
            v = take(2)
            if not v:
                break
            cx, cy = (cx + v[0], cy + v[1]) if rel else (v[0], v[1])
            pts.append((cx, cy))
        elif head == "A":
            v = take(7)
            if not v:
                break
            cx, cy = (cx + v[5], cy + v[6]) if rel else (v[5], v[6])
            pts.append((cx, cy))
        else:
            i += 1
    return pts


def _parse_transform(raw: str) -> tuple[float, float, float, float, float, float]:
    """SVG transform list as affine (a, b, c, d, e, f): [x'] = [a c e][x]; [y'] = [b d f][y]."""
    a, b, c, d, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    if not raw:
        return a, b, c, d, e, f
    for kind, body in re.findall(r"(translate|scale|matrix)\s*\(([^)]*)\)", raw, re.I):
        vals = _nums(body)
        name = kind.lower()
        if name == "translate":
            tx = vals[0] if vals else 0.0
            ty = vals[1] if len(vals) > 1 else 0.0
            na, nb, nc, nd, ne, nf = 1.0, 0.0, 0.0, 1.0, tx, ty
        elif name == "scale":
            sx = vals[0] if vals else 1.0
            sy = vals[1] if len(vals) > 1 else sx
            na, nb, nc, nd, ne, nf = sx, 0.0, 0.0, sy, 0.0, 0.0
        elif name == "matrix" and len(vals) >= 6:
            na, nb, nc, nd, ne, nf = vals[0], vals[1], vals[2], vals[3], vals[4], vals[5]
        else:
            continue
        # prepend: M = M * N (apply N first, matching SVG right-to-left lists)
        a, b, c, d, e, f = (
            a * na + c * nb,
            b * na + d * nb,
            a * nc + c * nd,
            b * nc + d * nd,
            a * ne + c * nf + e,
            b * ne + d * nf + f,
        )
    return a, b, c, d, e, f


def _mul(
    m: tuple[float, float, float, float, float, float],
    n: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = m
    na, nb, nc, nd, ne, nf = n
    return (
        a * na + c * nb,
        b * na + d * nb,
        a * nc + c * nd,
        b * nc + d * nd,
        a * ne + c * nf + e,
        b * ne + d * nf + f,
    )


def _apply(
    x: float,
    y: float,
    m: tuple[float, float, float, float, float, float],
) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _union(box: list[float] | None, x: float, y: float) -> list[float]:
    if box is None:
        return [x, y, x, y]
    box[0] = min(box[0], x)
    box[1] = min(box[1], y)
    box[2] = max(box[2], x)
    box[3] = max(box[3], y)
    return box


_TEXT_INHERIT = ("font-size", "text-anchor", "font-weight")

# Per-character width factors (multiples of font-size) for a generic sans face.
# Crude, but far closer than one flat factor — and the difference decides
# whether a label is detected as overflowing the frame.
_NARROW = set("iljtfr.,:;'\"|!()[]{}·-")
_WIDE = set("mwMW@")
_UPPER = set("ABCDEFGHIJKLMNOPQRSTUVXYZ")


def _style_lookup(style: str, key: str) -> str:
    match = re.search(rf"(?:^|;)\s*{re.escape(key)}\s*:\s*([^;]+)", style, re.I)
    return match.group(1).strip() if match else ""


def _inherit(el: ET.Element, inherited: dict) -> dict:
    merged = dict(inherited)
    style = el.attrib.get("style") or ""
    for key in _TEXT_INHERIT:
        value = el.attrib.get(key) or (_style_lookup(style, key) if style else "")
        if value:
            merged[key] = value
    return merged


def _font_size(inherited: dict) -> float:
    raw = str(inherited.get("font-size") or "").strip()
    nums = _nums(raw)
    if nums and nums[0] > 0:
        return nums[0]
    return 14.0


def text_width(text: str, size: float, weight: str = "") -> float:
    total = 0.0
    for ch in text:
        if ch in _NARROW:
            total += 0.30
        elif ch in _WIDE:
            total += 0.90
        elif ch in _UPPER:
            total += 0.68
        elif ch.isdigit():
            total += 0.58
        elif ch == " ":
            total += 0.30
        else:
            total += 0.54
    bold = "bold" in str(weight).lower() or (_nums(str(weight)) or [400])[0] >= 600
    return max(total, 0.6) * size * (1.06 if bold else 1.0)


def _text_extent(el: ET.Element, inherited: dict) -> tuple[float, float, float, float] | None:
    """Local-space box of a <text>, honoring inherited font-size and text-anchor."""
    attr = el.attrib
    try:
        x = float(attr.get("x", 0) or 0)
        y = float(attr.get("y", 0) or 0)
    except ValueError:
        return None
    size = _font_size(inherited)
    text = " ".join("".join(el.itertext()).split())
    if not text:
        return None
    width = text_width(text, size, inherited.get("font-weight", ""))
    anchor = str(inherited.get("text-anchor") or "start").strip().lower()
    if anchor == "middle":
        left = x - width / 2
    elif anchor == "end":
        left = x - width
    else:
        left = x
    return (left - 3.0, y - size * 0.88, left + width + 3.0, y + size * 0.32)


def _walk(
    el: ET.Element,
    m: tuple[float, float, float, float, float, float],
    box: list[float] | None,
    inherited: dict | None = None,
) -> list[float] | None:
    tag = _local(el.tag)
    if tag in {"defs", "clippath", "mask", "style", "title", "desc"}:
        return box
    m = _mul(m, _parse_transform(el.attrib.get("transform", "")))
    inherited = _inherit(el, inherited or {})
    attr = el.attrib

    def pt(x: float, y: float) -> None:
        nonlocal box
        ax, ay = _apply(x, y, m)
        box = _union(box, ax, ay)

    if tag == "circle":
        cx, cy, r = float(attr.get("cx", 0)), float(attr.get("cy", 0)), float(attr.get("r", 0))
        pt(cx - r, cy - r)
        pt(cx + r, cy + r)
    elif tag == "ellipse":
        cx, cy = float(attr.get("cx", 0)), float(attr.get("cy", 0))
        rx, ry = float(attr.get("rx", 0)), float(attr.get("ry", 0))
        pt(cx - rx, cy - ry)
        pt(cx + rx, cy + ry)
    elif tag in {"rect", "image"}:
        x, y = float(attr.get("x", 0)), float(attr.get("y", 0))
        w, h = float(attr.get("width", 0)), float(attr.get("height", 0))
        pt(x, y)
        pt(x + w, y + h)
    elif tag == "line":
        pt(float(attr.get("x1", 0)), float(attr.get("y1", 0)))
        pt(float(attr.get("x2", 0)), float(attr.get("y2", 0)))
    elif tag in {"polyline", "polygon"}:
        vals = _nums(attr.get("points", ""))
        for i in range(0, len(vals) - 1, 2):
            pt(vals[i], vals[i + 1])
    elif tag == "path":
        for px, py in path_points(attr.get("d", "")):
            pt(px, py)
    elif tag == "text":
        extent = _text_extent(el, inherited)
        if extent:
            pt(extent[0], extent[1])
            pt(extent[2], extent[3])
    for child in list(el):
        box = _walk(child, m, box, inherited)
    return box


def content_bbox(svg: str) -> tuple[float, float, float, float] | None:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return None
    ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    box = _walk(root, ident, None)
    if not box:
        return None
    return (box[0], box[1], box[2], box[3])


MAX_UPSCALE = 1.7
# Never let the drawing touch the safe-box edge exactly: leaves room for
# rounding in the transform and reads less cramped on a phone.
FIT_CUSHION = 0.96


def _unwrap_fit(root: ET.Element) -> None:
    """Remove a fit wrapper added by an earlier fit_svg pass.

    A reel's SVG is normalized more than once (the director's own output, then
    again when diagrams are filled in). Without this, each pass would wrap the
    previous one and the scales would multiply, so the drawing creeps larger
    and off-center every pass.
    """
    while True:
        kids = list(root)
        if len(kids) != 1:
            return
        only = kids[0]
        if _local(only.tag) != "g" or not only.attrib.get("data-fit"):
            return
        root.remove(only)
        for child in list(only):
            only.remove(child)
            root.append(child)


def fit_svg(svg: str, viewbox: tuple[float, float, float, float] | None = None, margin: float = REEL_MARGIN) -> str:
    """Always scale-to-fit AND center the drawing in the safe box. This runs
    unconditionally: 'close enough' centering is what made diagrams look
    unaligned from beat to beat. Idempotent — fitting twice equals fitting once."""
    vb = viewbox or parse_viewbox(svg)
    if not vb or vb[2] <= 0 or vb[3] <= 0:
        return ""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return ""
    _unwrap_fit(root)
    svg = _serialize(root)
    bbox = content_bbox(svg)
    if not bbox:
        return svg
    min_x, min_y, max_x, max_y = bbox
    bw = max(max_x - min_x, 1.0)
    bh = max(max_y - min_y, 1.0)
    vx, vy, vw, vh = vb
    inner_w = max(vw - 2 * margin, 1.0)
    inner_h = max(vh - 2 * margin, 1.0)
    scale = min(inner_w / bw * FIT_CUSHION, inner_h / bh * FIT_CUSHION, MAX_UPSCALE)
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    vcx, vcy = vx + vw / 2, vy + vh / 2
    transform = f"translate({vcx:.2f} {vcy:.2f}) scale({scale:.4f}) translate({-cx:.2f} {-cy:.2f})"
    group = ET.Element(f"{{{_NS}}}g" if root.tag.startswith("{") else "g")
    group.set("transform", transform)
    group.set("data-fit", "1")
    for child in list(root):
        root.remove(child)
        group.append(child)
    root.append(group)
    root.set("viewBox", f"{vx:g} {vy:g} {vw:g} {vh:g}")
    root.attrib.pop("width", None)
    root.attrib.pop("height", None)
    return _serialize(root)


_SKIP_GROUPING = {"defs", "style", "title", "desc", "clippath", "mask"}
_DRAWABLE = {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text"}
_STROKE_SHAPES = {"line", "polyline", "polygon", "circle", "ellipse", "rect", "path"}


def ensure_reveal_groups(svg: str) -> str:
    """Wrap each top-level shape in its own <g class="r-el"> so the frontend
    can stagger-reveal one idea at a time, regardless of whether the model
    already grouped things itself."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    original = list(root)
    new_children = []
    changed = False
    for child in original:
        tag = _local(child.tag)
        if tag in _SKIP_GROUPING:
            new_children.append(child)
        elif tag == "g":
            # If this <g> already contains its own r-el-tagged descendants, it's
            # a structural/centering wrapper the model added, not an idea group
            # — leave it untagged so the frontend doesn't double-stagger it on
            # top of its already-tagged children.
            has_nested_rel = any(
                desc is not child
                and _local(desc.tag) == "g"
                and "r-el" in (desc.attrib.get("class", "") or "").split()
                for desc in child.iter()
            )
            if not has_nested_rel:
                classes = (child.attrib.get("class", "") or "").split()
                if "r-el" not in classes:
                    classes.append("r-el")
                    child.set("class", " ".join(classes))
                    changed = True
            new_children.append(child)
        elif tag in _DRAWABLE:
            g_tag = f"{{{_NS}}}g" if root.tag.startswith("{") else "g"
            wrapper = ET.Element(g_tag, {"class": "r-el"})
            wrapper.append(child)
            new_children.append(wrapper)
            changed = True
        else:
            new_children.append(child)
    if not changed:
        return svg
    for child in original:
        root.remove(child)
    for child in new_children:
        root.append(child)
    return _serialize(root)


MIN_LABEL_SIZE = 9.0


def _shape_box(el: ET.Element, inherited: dict) -> tuple[float, float, float, float] | None:
    box = _walk(el, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), None, inherited)
    return tuple(box) if box else None  # type: ignore[return-value]


def fit_labels_to_shapes(svg: str) -> str:
    """Shrink a label that is wider than the shape it sits inside.

    Frame-level clamping is not enough: a caption centered in a box can sit
    well inside the frame while still spilling out of the box it belongs to,
    which is what reads as 'text flowing out'.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    # Collect candidate host shapes (boxes and round bodies) with their boxes.
    hosts: list[tuple[float, float, float, float]] = []

    def collect(el: ET.Element, inherited: dict) -> None:
        merged = _inherit(el, inherited)
        if _local(el.tag) in {"rect", "circle", "ellipse"}:
            box = _shape_box(el, merged)
            if box and (box[2] - box[0]) > 12 and (box[3] - box[1]) > 8:
                hosts.append(box)
        for child in list(el):
            collect(child, merged)

    collect(root, {})
    if not hosts:
        return svg

    changed = False

    def visit(el: ET.Element, inherited: dict) -> None:
        nonlocal changed
        merged = _inherit(el, inherited)
        if _local(el.tag) == "text":
            extent = _text_extent(el, merged)
            if extent:
                cx = (extent[0] + extent[2]) / 2
                cy = (extent[1] + extent[3]) / 2
                inside = [h for h in hosts if h[0] <= cx <= h[2] and h[1] <= cy <= h[3]]
                if inside:
                    host = min(inside, key=lambda h: (h[2] - h[0]) * (h[3] - h[1]))
                    allowed = (host[2] - host[0]) - 8  # a little padding inside the shape
                    width = extent[2] - extent[0]
                    if allowed > 8 and width > allowed:
                        size = _font_size(merged)
                        shrunk = max(MIN_LABEL_SIZE, size * (allowed / width))
                        if shrunk < size - 0.1:
                            el.set("font-size", f"{shrunk:.1f}")
                            changed = True
        for child in list(el):
            visit(child, merged)

    visit(root, {})
    return _serialize(root) if changed else svg


def _boxes_overlap(a: tuple, b: tuple) -> float:
    """Intersection as a fraction of the smaller box."""
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    if ox <= 0 or oy <= 0:
        return 0.0
    inter = ox * oy
    smaller = min(max((a[2] - a[0]) * (a[3] - a[1]), 1e-6), max((b[2] - b[0]) * (b[3] - b[1]), 1e-6))
    return inter / smaller


# Horizontal crowding of two separate labels is a defect; vertical spacing of
# ~1.2em is just normal line spacing, so the vertical tolerance is much smaller.
MIN_LABEL_GAP_X = 16.0
MIN_LABEL_GAP_Y = 12.0
BODY_GAP = 18.0
SAFE_BOX = (20.0, 20.0, 340.0, 420.0)


def overlap_report(
    svg: str, min_gap_x: float = MIN_LABEL_GAP_X, min_gap_y: float = MIN_LABEL_GAP_Y
) -> list[str]:
    """Describe label collisions so the renderer can be asked to redraw.

    Captions drawn on top of their own box still count — that is the overlap
    viewers notice. Arrows may touch the things they point at.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return []

    texts = _iter_drawn_texts(root)
    fills = _iter_fills(root)
    issues: list[str] = []

    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            t1, t2 = texts[i], texts[j]
            gap_x = max(t1["box"][0], t2["box"][0]) - min(t1["box"][2], t2["box"][2])
            gap_y = max(t1["box"][1], t2["box"][1]) - min(t1["box"][3], t2["box"][3])
            if gap_x >= min_gap_x or gap_y >= min_gap_y:
                continue
            touching = _boxes_overlap(t1["box"], t2["box"]) > 0
            how = "overlap" if touching else "are too close together"
            issues.append(f'the labels "{t1["label"]}" and "{t2["label"]}" {how}')

    for lab in texts:
        for body in fills:
            if not _aabb_hit(lab["box"], body["box"], min_gap_x * 0.4, min_gap_y * 0.4):
                continue
            if _boxes_overlap(lab["box"], body["box"]) < 0.08:
                continue
            issues.append(f'the label "{lab["label"]}" sits on another shape')

    for i in range(len(fills)):
        for j in range(i + 1, len(fills)):
            if not _aabb_hit(fills[i]["box"], fills[j]["box"], BODY_GAP * 0.4, BODY_GAP * 0.4):
                continue
            if _boxes_overlap(fills[i]["box"], fills[j]["box"]) < 0.12:
                continue
            issues.append("two filled shapes overlap")
    return issues


def _aabb_hit(a: tuple, b: tuple, gap_x: float, gap_y: float) -> bool:
    gx = max(a[0], b[0]) - min(a[2], b[2])
    gy = max(a[1], b[1]) - min(a[3], b[3])
    return gx < gap_x and gy < gap_y


def _is_rel(el: ET.Element) -> bool:
    return "r-el" in (el.attrib.get("class", "") or "").split()


def _group_kind(g: ET.Element) -> str:
    tags = {_local(child.tag) for child in g.iter()}
    has_text = "text" in tags
    has_body = bool(tags & {"rect", "circle", "ellipse"})
    has_line = bool(tags & {"line", "polyline", "path"})
    if has_body:
        return "body"
    if has_text and not has_line:
        return "label"
    if has_line:
        return "arrow"
    if has_text:
        return "label"
    return "other"


def _group_label(g: ET.Element) -> str:
    return " ".join("".join(g.itertext()).split())


def _layout_items(root: ET.Element) -> list[dict]:
    items: list[dict] = []
    ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def visit(el: ET.Element, parent_m: tuple, inherited: dict) -> None:
        if _local(el.tag) == "g" and _is_rel(el):
            nested = any(desc is not el and _local(desc.tag) == "g" and _is_rel(desc) for desc in el.iter())
            if not nested:
                box = _walk(el, parent_m, None, inherited)
                if box and box[2] > box[0] and box[3] > box[1]:
                    items.append(
                        {
                            "el": el,
                            "box": (box[0], box[1], box[2], box[3]),
                            "kind": _group_kind(el),
                            "label": _group_label(el),
                            "parent_m": parent_m,
                        }
                    )
        m2 = _mul(parent_m, _parse_transform(el.attrib.get("transform", "")))
        merged = _inherit(el, inherited)
        for child in list(el):
            visit(child, m2, merged)

    visit(root, ident, {})
    return items


def _skip_layout(tag: str) -> bool:
    return tag in {"defs", "clippath", "mask", "style", "title", "desc"}


def _iter_drawn_texts(root: ET.Element) -> list[dict]:
    """Every <text>, including captions sitting inside a body group."""
    items: list[dict] = []
    ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def visit(el: ET.Element, parent_m: tuple, inherited: dict) -> None:
        tag = _local(el.tag)
        if _skip_layout(tag):
            return
        merged = _inherit(el, inherited)
        if tag == "text":
            box = _walk(el, parent_m, None, inherited)
            label = " ".join("".join(el.itertext()).split())
            if box and label and box[2] > box[0] and box[3] > box[1]:
                items.append(
                    {
                        "el": el,
                        "box": (box[0], box[1], box[2], box[3]),
                        "parent_m": parent_m,
                        "label": label,
                        "kind": "label",
                    }
                )
        m2 = _mul(parent_m, _parse_transform(el.attrib.get("transform", "")))
        for child in list(el):
            visit(child, m2, merged)

    visit(root, ident, {})
    return items


def _iter_fills(root: ET.Element) -> list[dict]:
    items: list[dict] = []
    ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def visit(el: ET.Element, parent_m: tuple, inherited: dict) -> None:
        tag = _local(el.tag)
        if _skip_layout(tag):
            return
        merged = _inherit(el, inherited)
        if tag in {"rect", "circle", "ellipse"}:
            fill = (el.attrib.get("fill") or "").strip().lower()
            if fill and fill != "none":
                box = _walk(el, parent_m, None, inherited)
                if box and (box[2] - box[0]) > 8 and (box[3] - box[1]) > 8:
                    items.append(
                        {
                            "el": el,
                            "box": (box[0], box[1], box[2], box[3]),
                            "parent_m": parent_m,
                        }
                    )
        m2 = _mul(parent_m, _parse_transform(el.attrib.get("transform", "")))
        for child in list(el):
            visit(child, m2, merged)

    visit(root, ident, {})
    return items


def _union_boxes(boxes: list[tuple]) -> tuple | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _descendant(parent: ET.Element, child: ET.Element) -> bool:
    return any(desc is child for desc in parent.iter())


def _body_hitboxes(root: ET.Element) -> list[dict]:
    """r-el bodies measured by filled shapes only, not captions in the same group."""
    fills = _iter_fills(root)
    out: list[dict] = []
    for item in _layout_items(root):
        if item["kind"] != "body":
            continue
        owned = [f["box"] for f in fills if _descendant(item["el"], f["el"])]
        hit = _union_boxes(owned)
        if not hit:
            continue
        row = dict(item)
        row["box"] = hit
        out.append(row)
    return out


def _screen_to_local(parent_m: tuple, sdx: float, sdy: float) -> tuple[float, float]:
    """Convert a screen-space shift into the group's own coordinate space.

    Groups live inside fit_svg's scale wrapper. A 40px gap on screen is not a
    translate(40) on the element — it has to be divided by that scale.
    """
    a, b, c, d, _e, _f = parent_m
    det = a * d - b * c
    if abs(det) < 1e-8:
        return sdx, sdy
    return (d * sdx - c * sdy) / det, (-b * sdx + a * sdy) / det


def _read_translate(el: ET.Element) -> tuple[float, float]:
    tx = ty = 0.0
    raw = el.attrib.get("transform") or ""
    for kind, body in re.findall(r"(translate|scale|matrix)\s*\(([^)]*)\)", raw, re.I):
        if kind.lower() != "translate":
            continue
        vals = _nums(body)
        tx += vals[0] if vals else 0.0
        ty += vals[1] if len(vals) > 1 else 0.0
    return tx, ty


def _nudge_group(el: ET.Element, parent_m: tuple, sdx: float, sdy: float) -> bool:
    """Move a group by a screen-space delta, stored as one composed translate."""
    if abs(sdx) < 0.05 and abs(sdy) < 0.05:
        return False
    ldx, ldy = _screen_to_local(parent_m, sdx, sdy)
    if abs(ldx) < 0.05 and abs(ldy) < 0.05:
        return False
    ox, oy = _read_translate(el)
    el.set("transform", f"translate({ox + ldx:.1f} {oy + ldy:.1f})")
    return True


def _clamp_nudge(box: tuple, dx: float, dy: float) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    sx0, sy0, sx1, sy1 = SAFE_BOX
    if x0 + dx < sx0:
        dx = sx0 - x0
    if y0 + dy < sy0:
        dy = sy0 - y0
    if x1 + dx > sx1:
        dx = sx1 - x1
    if y1 + dy > sy1:
        dy = sy1 - y1
    return dx, dy


def _shifted(box: tuple, dx: float, dy: float) -> tuple[float, float, float, float]:
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def _push_apart(a: tuple, b: tuple, gap: float) -> tuple[float, float]:
    """How far to move box b so it clears box a by `gap` on one axis."""
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    acx, acy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    need_x = (ox + gap) if ox > -gap else 0.0
    need_y = (oy + gap) if oy > -gap else 0.0
    if need_x <= 0 and need_y <= 0:
        return 0.0, 0.0
    # Prefer the cheaper axis so we scatter instead of always stacking down.
    if need_x and (not need_y or need_x <= need_y):
        return (need_x if bcx >= acx else -need_x, 0.0)
    if need_y:
        return (0.0, need_y if bcy >= acy else -need_y)
    return 0.0, 0.0


def _hits_any(box: tuple, others: list[tuple], gap_x: float, gap_y: float) -> bool:
    return any(_aabb_hit(box, other, gap_x, gap_y) for other in others)


def _label_candidates(index: int) -> list[tuple[float, float]]:
    """Screen-space offsets that scatter labels across the tall phone."""
    lanes = [
        (-120.0, -70.0),
        (120.0, -70.0),
        (-120.0, 70.0),
        (120.0, 70.0),
        (0.0, -100.0),
        (0.0, 100.0),
        (-140.0, 0.0),
        (140.0, 0.0),
        (-90.0, -140.0),
        (90.0, -140.0),
        (-90.0, 140.0),
        (90.0, 140.0),
        (-160.0, -160.0),
        (160.0, 160.0),
    ]
    # Rotate preferred lanes so stacked labels don't all pick the same hole.
    rotated = lanes[index % len(lanes) :] + lanes[: index % len(lanes)]
    spiral: list[tuple[float, float]] = []
    for radius in range(20, 400, 12):
        step = 30 if radius < 80 else 15
        for deg in range(0, 360, step):
            rad = math.radians(deg)
            spiral.append((radius * math.cos(rad), radius * math.sin(rad)))
    return rotated + spiral


def _place_label(item: dict, blockers: list[tuple]) -> bool:
    box = item["box"]
    for raw_dx, raw_dy in _label_candidates(abs(hash(item["label"])) % 12):
        sdx, sdy = _clamp_nudge(box, raw_dx, raw_dy)
        if abs(sdx) + abs(sdy) < 8 and abs(raw_dx) + abs(raw_dy) >= 8:
            continue
        trial = _shifted(box, sdx, sdy)
        if _hits_any(trial, blockers, MIN_LABEL_GAP_X, MIN_LABEL_GAP_Y):
            continue
        return _nudge_group(item["el"], item["parent_m"], sdx, sdy)
    return False


def untangle_layout(svg: str) -> str:
    """Nudge colliding r-el groups apart in the final 360×440 frame.

    Arrows may still touch the objects they point at. Labels and filled
    bodies must not sit on top of each other.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    moved = False
    for _ in range(10):
        shifted = False
        bodies = _body_hitboxes(root)
        for i, left in enumerate(bodies):
            for right in bodies[i + 1 :]:
                if not _aabb_hit(left["box"], right["box"], BODY_GAP, BODY_GAP):
                    continue
                dx, dy = _push_apart(left["box"], right["box"], BODY_GAP)
                dx, dy = _clamp_nudge(right["box"], dx, dy)
                target = right
                if abs(dx) < 0.4 and abs(dy) < 0.4:
                    dx, dy = _push_apart(right["box"], left["box"], BODY_GAP)
                    dx, dy = _clamp_nudge(left["box"], dx, dy)
                    target = left
                if abs(dx) < 0.4 and abs(dy) < 0.4:
                    continue
                if _nudge_group(target["el"], target["parent_m"], dx, dy):
                    shifted = True
                    moved = True
        if not shifted:
            break

    for _ in range(3):
        fills = [it["box"] for it in _iter_fills(root)]
        labels = _iter_drawn_texts(root)
        placed: list[tuple] = []
        round_moved = False
        for lab in labels:
            blockers = fills + placed
            needs = _hits_any(lab["box"], blockers, MIN_LABEL_GAP_X, MIN_LABEL_GAP_Y)
            if not needs:
                placed.append(lab["box"])
                continue
            if _place_label(lab, blockers):
                moved = True
                round_moved = True
                for fresh in _iter_drawn_texts(root):
                    if fresh["el"] is lab["el"]:
                        placed.append(fresh["box"])
                        break
                else:
                    placed.append(lab["box"])
            else:
                placed.append(lab["box"])
        if not round_moved:
            break

    return _serialize(root) if moved else svg


def clamp_text(svg: str, viewbox: tuple[float, float, float, float] | None = None, margin: float = REEL_MARGIN) -> str:
    """Shrink any single label that is wider than the safe box, in place.

    Without this, one long label forces fit_svg to scale the whole diagram
    down to fit it, so a stray 30-character caption shrinks every shape on
    the beat. Clamping the offender first keeps the drawing at full size.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    vb = viewbox or parse_viewbox(svg) or REEL_VIEWBOX
    allowed = max(vb[2] - 2 * margin, 1.0)
    changed = False

    def visit(el: ET.Element, inherited: dict) -> None:
        nonlocal changed
        merged = _inherit(el, inherited)
        if _local(el.tag) == "text":
            extent = _text_extent(el, merged)
            if extent:
                width = extent[2] - extent[0]
                if width > allowed:
                    size = _font_size(merged)
                    shrunk = max(MIN_LABEL_SIZE, size * (allowed / width))
                    if shrunk < size - 0.1:
                        el.set("font-size", f"{shrunk:.1f}")
                        changed = True
        for child in list(el):
            visit(child, merged)

    visit(root, {})
    if not changed:
        return svg
    return _serialize(root)


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def _polyline_length(points: str) -> float:
    vals = _nums(points)
    total = 0.0
    for i in range(0, len(vals) - 3, 2):
        total += _distance(vals[i], vals[i + 1], vals[i + 2], vals[i + 3])
    return total


def _shape_length(tag: str, attr: dict) -> float:
    if tag == "line":
        return _distance(
            float(attr.get("x1", 0) or 0),
            float(attr.get("y1", 0) or 0),
            float(attr.get("x2", 0) or 0),
            float(attr.get("y2", 0) or 0),
        )
    if tag in {"polyline", "polygon"}:
        return _polyline_length(attr.get("points", ""))
    if tag == "circle":
        return 2 * math.pi * float(attr.get("r", 0) or 0)
    if tag == "ellipse":
        rx, ry = float(attr.get("rx", 0) or 0), float(attr.get("ry", 0) or 0)
        if rx + ry <= 0:
            return 0.0
        h = ((rx - ry) ** 2) / ((rx + ry) ** 2)
        return math.pi * (rx + ry) * (1 + (3 * h) / (10 + math.sqrt(4 - 3 * h)))
    if tag == "rect":
        return 2 * (float(attr.get("width", 0) or 0) + float(attr.get("height", 0) or 0))
    if tag == "path":
        pts = path_points(attr.get("d", ""))
        return sum(
            _distance(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
            for i in range(len(pts) - 1)
        )
    return 0.0


def annotate_stroke_draw(svg: str) -> str:
    """Give every stroke-only shape (line/arrow/ring, not a filled box) a
    data-length + matching stroke-dasharray/dashoffset sized to its own
    length, so it renders fully undrawn and ready for a draw-on animation."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    changed = False
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in _STROKE_SHAPES:
            continue
        stroke = (el.attrib.get("stroke") or "").strip().lower()
        fill = (el.attrib.get("fill") or "").strip().lower()
        if not stroke or stroke == "none":
            continue
        if fill and fill != "none":
            continue
        length = _shape_length(tag, el.attrib)
        if length <= 0:
            continue
        length = round(length, 1)
        el.set("data-length", f"{length:g}")
        changed = True
    if not changed:
        return svg
    return _serialize(root)


def normalize_reel_svg(raw: str) -> str:
    svg = sanitize_svg_markup((raw or "").strip())
    if not svg:
        return ""
    if rejects_absolute_layout(svg) and parse_viewbox(svg) is None:
        return ""
    if parse_viewbox(svg) is None:
        return ""
    svg = ensure_reveal_groups(svg)
    svg = fit_labels_to_shapes(svg)
    svg = clamp_text(svg, REEL_VIEWBOX, REEL_MARGIN)
    # Text width/height here is an estimate, not real font metrics, so fit into
    # a slightly tighter box than the true safe area. That slack absorbs the
    # estimation error instead of letting a label graze the frame edge.
    # Always the tall canvas: a model that emitted the old 360x300 box still
    # gets re-centered into the portrait frame the phone actually has.
    fitted = fit_svg(svg, REEL_VIEWBOX, REEL_MARGIN + 6.0)
    if not fitted:
        return ""
    if parse_viewbox(fitted) is None:
        return ""
    fitted = untangle_layout(fitted)
    return annotate_stroke_draw(fitted)
