import math
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
