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
