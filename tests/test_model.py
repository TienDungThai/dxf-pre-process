import pytest
from dxf_cleaner.model import Segment, Contour, Part, Diagnostic, scale_contour


def test_segment_line_defaults_are_none():
    seg = Segment(kind="line", start=(0, 0), end=(1, 0))
    assert seg.center is None
    assert seg.radius is None
    assert seg.ccw is None


def test_contour_holds_segments_and_metadata():
    seg = Segment(kind="line", start=(0, 0), end=(1, 0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="ABC")
    assert contour.segments == [seg]
    assert contour.source_handle == "ABC"


def test_part_defaults_to_no_interiors():
    ext = Contour(segments=[], is_closed=True, source_layer="0", source_handle="1")
    part = Part(exterior=ext)
    assert part.interiors == []


def test_scale_contour_scales_line_segment():
    seg = Segment(kind="line", start=(1.0, 2.0), end=(3.0, 4.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    scaled = scale_contour(contour, 25.4)
    assert scaled.segments[0].start == pytest.approx((25.4, 50.8))
    assert scaled.segments[0].end == pytest.approx((76.2, 101.6))
    # original untouched
    assert contour.segments[0].start == (1.0, 2.0)


def test_scale_contour_scales_arc_radius_and_center():
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    scaled = scale_contour(contour, 2.0)
    assert scaled.segments[0].radius == 2.0
    assert scaled.segments[0].center == (0.0, 0.0)
    assert scaled.segments[0].ccw is True


import math
from shapely.geometry import LinearRing, LineString
from dxf_cleaner.model import discretize_arc, discretize_contour


def test_discretize_arc_quarter_circle_endpoints_match():
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    points = discretize_arc(seg, tolerance=0.01)
    assert points[0] == (1.0, 0.0)
    assert math.isclose(points[-1][0], 0.0, abs_tol=1e-9)
    assert math.isclose(points[-1][1], 1.0, abs_tol=1e-9)


def test_discretize_arc_respects_chord_tolerance():
    # Large radius -> few points needed to stay within tolerance.
    seg = Segment(kind="arc", start=(100.0, 0.0), end=(0.0, 100.0), center=(0.0, 0.0), radius=100.0, ccw=True)
    points = discretize_arc(seg, tolerance=0.02)
    # Verify every point is within tolerance of the true circle.
    for x, y in points:
        dist = math.hypot(x, y)
        assert abs(dist - 100.0) < 1e-6  # points sit exactly on the circle by construction
    # Verify the max sagitta (midpoint deviation) between consecutive samples respects tolerance.
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        mid = ((x1 + x2) / 2, (y1 + y2) / 2)
        mid_dist_to_center = math.hypot(*mid)
        sagitta = 100.0 - mid_dist_to_center
        assert sagitta <= 0.02 + 1e-9


def test_discretize_arc_clockwise():
    seg = Segment(kind="arc", start=(0.0, 1.0), end=(1.0, 0.0), center=(0.0, 0.0), radius=1.0, ccw=False)
    points = discretize_arc(seg, tolerance=0.01)
    assert points[0] == (0.0, 1.0)
    assert math.isclose(points[-1][0], 1.0, abs_tol=1e-9)
    assert math.isclose(points[-1][1], 0.0, abs_tol=1e-9)


def test_discretize_contour_line_and_arc_chain_dedupes_shared_vertex():
    line = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    arc = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    contour = Contour(segments=[line, arc], is_closed=False, source_layer="0", source_handle="1")
    points = discretize_contour(contour, tolerance=0.01)
    # (1.0, 0.0) must appear exactly once (shared between line end and arc start)
    assert points.count((1.0, 0.0)) == 1


def test_contour_to_shapely_closed_gives_linear_ring():
    seg1 = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    seg2 = Segment(kind="line", start=(1.0, 0.0), end=(0.0, 0.0))
    contour = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="1")
    ring = contour.to_shapely(arc_tolerance=0.01)
    assert isinstance(ring, LinearRing)


def test_contour_to_shapely_open_gives_line_string():
    seg = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    line = contour.to_shapely(arc_tolerance=0.01)
    assert isinstance(line, LineString)


# Task 4: contour_bbox, contour_signed_area, contour_as_full_circle
from dxf_cleaner.model import contour_bbox, contour_signed_area, contour_as_full_circle


def _unit_square_ccw() -> Contour:
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    segs = [Segment(kind="line", start=pts[i], end=pts[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle="1")


def test_contour_bbox_unit_square():
    assert contour_bbox(_unit_square_ccw()) == (0.0, 0.0, 1.0, 1.0)


def test_contour_signed_area_ccw_is_positive():
    assert math.isclose(contour_signed_area(_unit_square_ccw()), 1.0, abs_tol=1e-9)


def test_contour_signed_area_cw_is_negative():
    square = _unit_square_ccw()
    square.segments = list(reversed([
        Segment(kind="line", start=s.end, end=s.start) for s in square.segments
    ]))
    assert math.isclose(contour_signed_area(square), -1.0, abs_tol=1e-9)


def test_contour_as_full_circle_detects_two_arc_circle():
    center = (0.0, 0.0)
    radius = 5.0
    seg1 = Segment(kind="arc", start=(5.0, 0.0), end=(-5.0, 0.0), center=center, radius=radius, ccw=True)
    seg2 = Segment(kind="arc", start=(-5.0, 0.0), end=(5.0, 0.0), center=center, radius=radius, ccw=True)
    contour = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="1")
    result = contour_as_full_circle(contour)
    assert result is not None
    got_center, got_radius = result
    assert math.isclose(got_center[0], 0.0, abs_tol=1e-9)
    assert math.isclose(got_radius, 5.0, abs_tol=1e-9)


def test_contour_as_full_circle_rejects_non_circle():
    assert contour_as_full_circle(_unit_square_ccw()) is None
