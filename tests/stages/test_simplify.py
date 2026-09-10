import math
from dxf_cleaner.model import Segment, Contour, contour_signed_area
from dxf_cleaner.stages.simplify import simplify_contours


def _dense_square(size=10, handle="A", subdivisions=20):
    """A square whose 4 edges are each subdivided into many collinear segments —
    the kind of over-noded boundary weld/flatten produce."""
    corners = [(0, 0), (size, 0), (size, size), (0, size)]
    points: list[tuple[float, float]] = []
    for i in range(4):
        p0, p1 = corners[i], corners[(i + 1) % 4]
        for k in range(subdivisions):
            t = k / subdivisions
            points.append((p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))
    segs = [Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)]) for i in range(len(points))]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


def test_untouched_contour_is_left_alone():
    dense = _dense_square(handle="A")
    result, diags = simplify_contours([dense], touched_handles=set(), tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert result == [dense]
    assert diags == []


def test_touched_contour_gets_node_count_reduced():
    dense = _dense_square(handle="A")
    before_count = len(dense.segments)
    result, diags = simplify_contours([dense], touched_handles={"A"}, tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert len(result) == 1
    assert len(result[0].segments) < before_count
    assert len(result[0].segments) == 4  # collinear merge collapses each edge back to one segment


def test_area_preserved_within_tolerance():
    dense = _dense_square(handle="A", size=10)
    before_area = abs(contour_signed_area(dense))
    result, diags = simplify_contours([dense], touched_handles={"A"}, tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    after_area = abs(contour_signed_area(result[0]))
    assert abs(after_area - before_area) / before_area < 0.001


def test_reverts_when_area_deviation_exceeds_threshold():
    dense = _dense_square(handle="A")
    # tolerance so large Douglas-Peucker cuts a real corner off (verified: at 5.0 the
    # square's corners survive since they are the max-deviation points; 8.0 is the
    # smallest tolerance that actually collapses the (0,0) corner) -> big area change
    result, diags = simplify_contours([dense], touched_handles={"A"}, tolerance=8.0,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert result == [dense]
    assert any(d.code == "SIMPLIFY_REVERTED_AREA_DEVIATION" for d in diags)


def test_arc_segments_in_touched_contour_are_preserved_as_arcs():
    seg1 = Segment(kind="arc", start=(10, 0), end=(-10, 0), center=(0, 0), radius=10, ccw=True)
    seg2 = Segment(kind="arc", start=(-10, 0), end=(10, 0), center=(0, 0), radius=10, ccw=True)
    circle = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="A")
    result, diags = simplify_contours([circle], touched_handles={"A"}, tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert len(result) == 1
    assert all(s.kind == "arc" for s in result[0].segments)
