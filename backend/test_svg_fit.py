from svg_fit import content_bbox, normalize_reel_svg, overlap_report, parse_viewbox, rejects_absolute_layout, sanitize_svg_markup


OVERFLOW = """<svg viewBox="0 0 360 300">
  <rect x="-80" y="-20" width="520" height="400" fill="#d7e8f8"/>
  <text x="8" y="20" font-size="14">old bond</text>
</svg>"""

GOOD = """<svg viewBox="0 0 360 300">
  <rect x="40" y="90" width="110" height="80" fill="#d7e8f8"/>
  <rect x="210" y="90" width="110" height="80" fill="#fff7e8"/>
</svg>"""


def test_reject_missing_viewbox():
    assert rejects_absolute_layout('<svg width="360px" height="300px"><rect x="0" y="0" width="10" height="10"/></svg>')
    assert normalize_reel_svg('<svg width="360px"><rect x="0" y="0" width="10" height="10"/></svg>') == ""


def test_good_stays_put():
    # Content authored on the old 360x300 canvas is re-centered into the tall
    # portrait frame the phone actually has.
    fitted = normalize_reel_svg(GOOD)
    assert parse_viewbox(fitted) == (0.0, 0.0, 360.0, 440.0)
    box = content_bbox(fitted)
    assert box is not None
    assert box[0] >= 20
    assert box[2] <= 340
    cy = (box[1] + box[3]) / 2
    assert abs(cy - 220) < 2


def test_overflow_is_recentered():
    fitted = normalize_reel_svg(OVERFLOW)
    assert fitted
    box = content_bbox(fitted)
    assert box is not None
    assert box[0] >= 15
    assert box[1] >= 15
    assert box[2] <= 345
    assert box[3] <= 425
    vb = parse_viewbox(fitted)
    assert vb == (0.0, 0.0, 360.0, 440.0)
    cx = (box[0] + box[2]) / 2
    assert abs(cx - 180) < 25


NS0 = """<ns0:svg xmlns:ns0="http://www.w3.org/2000/svg" viewBox="0 0 360 300">
  <g transform="translate(180.00 150.00) scale(0.9144) translate(-202.97 -138.00)">
    <ns0:g class="r-el">
      <ns0:rect x="28" y="42" width="90" height="52" rx="8" fill="#EEF3FA" />
      <ns0:text x="73" y="73" text-anchor="middle" font-size="13" fill="#1A2F4D">desk positions</ns0:text>
    </ns0:g>
    <ns0:line x1="118" y1="68" x2="202" y2="150" stroke="#1A2F4D" stroke-width="3" fill="none"
      data-length="117.4" stroke-dasharray="117.4" stroke-dashoffset="117.4" />
  </g>
</ns0:svg>"""


def test_ns0_and_baked_dash_are_stripped():
    cleaned = sanitize_svg_markup(NS0)
    assert "<svg" in cleaned
    assert "ns0:" not in cleaned
    assert "stroke-dashoffset" not in cleaned
    fitted = normalize_reel_svg(NS0)
    assert fitted
    assert "<svg" in fitted
    assert "ns0:" not in fitted
    assert "stroke-dashoffset" not in fitted
    assert "r-el" in fitted
    assert "desk positions" in fitted


PILED = """<svg viewBox="0 0 360 440">
  <g class="r-el"><rect x="80" y="160" width="200" height="100" fill="#d7e8f8"/></g>
  <g class="r-el"><rect x="90" y="170" width="200" height="100" fill="#fff7e8"/></g>
  <g class="r-el"><text x="180" y="210" font-size="16" text-anchor="middle">hello</text></g>
  <g class="r-el"><text x="184" y="214" font-size="16" text-anchor="middle">world</text></g>
</svg>"""


ON_BOX = """<svg viewBox="0 0 360 440">
  <g class="r-el">
    <rect x="40" y="80" width="280" height="120" fill="#d7e8f8"/>
    <text x="180" y="150" font-size="16" text-anchor="middle">old bond</text>
  </g>
  <g class="r-el">
    <rect x="40" y="90" width="280" height="120" fill="#fff7e8"/>
    <text x="180" y="155" font-size="16" text-anchor="middle">new bond</text>
  </g>
</svg>"""


def test_piled_drawing_is_untangled():
    assert overlap_report(PILED)
    fitted = normalize_reel_svg(PILED)
    assert fitted
    leftover = overlap_report(fitted)
    assert leftover == [], leftover


def test_captions_on_boxes_are_moved_off():
    assert overlap_report(ON_BOX)
    fitted = normalize_reel_svg(ON_BOX)
    leftover = overlap_report(fitted)
    assert leftover == [], leftover


if __name__ == "__main__":
    test_reject_missing_viewbox()
    test_good_stays_put()
    test_overflow_is_recentered()
    test_ns0_and_baked_dash_are_stripped()
    test_piled_drawing_is_untangled()
    test_captions_on_boxes_are_moved_off()
    print("ok")
