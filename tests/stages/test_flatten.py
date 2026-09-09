import math
import pytest
from dxf_cleaner.model import contour_as_full_circle
from dxf_cleaner.stages.flatten import flatten_entity


def test_flatten_open_spline_produces_line_segments(new_doc):
    msp = new_doc.modelspace()
    spline = msp.add_spline(
        fit_points=[(0, 0), (5, 5), (10, 0), (15, 5)], degree=3
    )
    spline.dxf.layer = "0"
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=False)
    assert contour.is_closed is False
    assert all(seg.kind == "line" for seg in contour.segments)
    assert math.isclose(contour.segments[0].start[0], 0, abs_tol=1e-9)
    assert math.isclose(contour.segments[-1].end[0], 15, abs_tol=0.5)


def test_flatten_ellipse_produces_line_segments(new_doc):
    msp = new_doc.modelspace()
    ellipse = msp.add_ellipse(center=(0, 0), major_axis=(5, 0), ratio=0.5)
    contour = flatten_entity(ellipse, chord_tolerance=0.02, detect_circular=False)
    assert all(seg.kind == "line" for seg in contour.segments)
    # a full ellipse loop should start and end at (nearly) the same point
    assert math.isclose(contour.segments[0].start[0], contour.segments[-1].end[0], abs_tol=1e-6)
    assert math.isclose(contour.segments[0].start[1], contour.segments[-1].end[1], abs_tol=1e-6)


def test_flatten_uses_source_handle_and_layer(new_doc):
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=[(0, 0), (1, 1), (2, 0)], degree=2, dxfattribs={"layer": "MYLAYER"})
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=False)
    assert contour.source_layer == "MYLAYER"
    assert contour.source_handle == spline.dxf.handle


def _points_on_arc(cx, cy, r, a0_deg, a1_deg, n=20):
    a0, a1 = math.radians(a0_deg), math.radians(a1_deg)
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / (n - 1)), cy + r * math.sin(a0 + (a1 - a0) * i / (n - 1))) for i in range(n)]


def test_spline_that_is_really_a_quarter_circle_is_detected_as_arc(new_doc):
    pts = _points_on_arc(0, 0, 10, 0, 90)
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts, degree=3, dxfattribs={"layer": "0"})
    # Monkeypatch flattening to return our exact arc points regardless of fit accuracy,
    # isolating the circle-detection logic from spline-fitting noise.
    spline.flattening = lambda distance, segments=4: (type("P", (), {"x": x, "y": y})() for x, y in pts)
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert len(contour.segments) == 1
    seg = contour.segments[0]
    assert seg.kind == "arc"
    assert math.isclose(seg.radius, 10.0, abs_tol=0.02)
    assert contour.is_closed is False


def test_spline_that_is_really_a_full_circle_is_detected_as_two_arc_circle(new_doc):
    pts = _points_on_arc(0, 0, 10, 0, 360, n=40)
    pts[-1] = pts[0]  # close the loop exactly
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts[:-1], degree=3, dxfattribs={"layer": "0"})
    spline.flattening = lambda distance, segments=4: (type("P", (), {"x": x, "y": y})() for x, y in pts)
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert contour.is_closed is True
    result = contour_as_full_circle(contour, tolerance=0.02)
    assert result is not None
    center, radius = result
    assert math.isclose(radius, 10.0, abs_tol=0.02)


def test_non_circular_spline_falls_back_to_line_segments(new_doc):
    pts = [(0, 0), (5, 5), (10, 0), (15, 5)]
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts, degree=3, dxfattribs={"layer": "0"})
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert all(seg.kind == "line" for seg in contour.segments)


def test_three_collinear_points_do_not_crash_circle_fit(new_doc):
    pts = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts, degree=1, dxfattribs={"layer": "0"})
    spline.flattening = lambda distance, segments=4: (type("P", (), {"x": x, "y": y})() for x, y in pts)
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert all(seg.kind == "line" for seg in contour.segments)


def _fake_flattening(spline, pts):
    spline.flattening = lambda distance, segments=4: (
        type("P", (), {"x": x, "y": y})() for x, y in pts
    )


def _sampled_arc_points(seg, n=25):
    """Sample points along the arc a Segment claims to describe."""
    cx, cy = seg.center
    a0 = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    a1 = math.atan2(seg.end[1] - cy, seg.end[0] - cx)
    two_pi = 2 * math.pi
    sweep = (a1 - a0) % two_pi if seg.ccw else -((a0 - a1) % two_pi)
    return [
        (cx + seg.radius * math.cos(a0 + sweep * i / n),
         cy + seg.radius * math.sin(a0 + sweep * i / n))
        for i in range(n + 1)
    ]


@pytest.mark.parametrize("ccw_input", [True, False])
def test_off_origin_circular_arc_is_not_mirrored(new_doc, ccw_input):
    """Regression: winding was computed about the origin, not the arc's center,
    so an arc centred away from (0,0) was reconstructed as its mirror image."""
    cx, cy, r = 100.0, 0.0, 1.0
    # 90 deg -> 270 deg going CCW passes through the LEFT side (x < cx).
    start_deg, end_deg = (90, 270) if ccw_input else (270, 90)
    pts = _points_on_arc(cx, cy, r, start_deg, end_deg, n=30)
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts[:4], degree=3, dxfattribs={"layer": "0"})
    _fake_flattening(spline, pts)

    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert len(contour.segments) == 1
    seg = contour.segments[0]
    assert seg.kind == "arc"
    assert math.isclose(seg.radius, r, abs_tol=0.02)

    # The true arc lies entirely on the left half: bbox x in [99, 100].
    xs = [p[0] for p in pts]
    assert math.isclose(min(xs), 99.0, abs_tol=0.01)
    assert math.isclose(max(xs), 100.0, abs_tol=1e-6)

    # Every point on the arc the Segment claims must lie on the input curve
    # (same half), not on the mirror arc across the chord.
    sampled = _sampled_arc_points(seg)
    assert max(p[0] for p in sampled) <= 100.0 + 1e-6, "arc was mirrored across the chord"
    assert seg.ccw is ccw_input
