import math
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
