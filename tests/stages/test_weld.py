import math
import pytest
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.weld import weld_contours


def _square(x0, y0, size, layer="0", handle="H1"):
    x1, y1 = x0 + size, y0 + size
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    segs = [Segment(kind="line", start=pts[i], end=pts[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer=layer, source_handle=handle)


def test_mode_off_returns_contours_unchanged():
    squares = [_square(0, 0, 10, handle="A"), _square(5, 5, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="off")
    assert result == squares
    assert diags == []
    assert welded == set()


def test_non_overlapping_squares_untouched_in_overlapping_mode():
    squares = [_square(0, 0, 10, handle="A"), _square(100, 100, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="overlapping")
    assert len(result) == 2
    assert {c.source_handle for c in result} == {"A", "B"}
    assert welded == set()


def test_overlapping_squares_are_merged_into_one_contour():
    squares = [_square(0, 0, 10, handle="A"), _square(5, 0, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="overlapping")
    assert len(result) == 1
    merged = result[0]
    assert merged.is_closed
    assert merged.source_handle in welded
    # union of two overlapping 10x10 squares offset by 5 has area 150
    from dxf_cleaner.model import contour_signed_area
    assert abs(abs(contour_signed_area(merged)) - 150.0) < 0.5


def test_touching_but_not_overlapping_squares_stay_independent():
    # share only an edge (zero-area intersection) -> not "genuinely overlapping"
    squares = [_square(0, 0, 10, handle="A"), _square(10, 0, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="overlapping")
    assert len(result) == 2
    assert welded == set()


def test_mode_all_welds_even_non_overlapping_shapes():
    squares = [_square(0, 0, 10, handle="A"), _square(100, 100, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="all")
    assert len(result) == 2  # MultiPolygon -> one Contour per disjoint piece
    assert all(c.source_handle in welded for c in result)


def test_open_contours_pass_through_untouched():
    open_line = Contour(
        segments=[Segment(kind="line", start=(0, 0), end=(1, 1))],
        is_closed=False, source_layer="0", source_handle="OPEN",
    )
    result, diags, welded = weld_contours([open_line], mode="overlapping")
    assert result == [open_line]
    assert welded == set()


def test_welded_ring_is_contiguous_for_circular_input():
    # A discretized circle's re-welded arc detection must produce a contiguous
    # contour even when the ring's boundary wraps past its closing point.
    n = 20
    r = 10.0
    pts = [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    segs = [Segment(kind="line", start=pts[i], end=pts[(i + 1) % n]) for i in range(n)]
    circle_a = Contour(segments=segs, is_closed=True, source_layer="0", source_handle="A")

    # A second, overlapping circle to force a weld + re-detect-arcs pass.
    pts_b = [(x + 5, y) for x, y in pts]
    segs_b = [Segment(kind="line", start=pts_b[i], end=pts_b[(i + 1) % n]) for i in range(n)]
    circle_b = Contour(segments=segs_b, is_closed=True, source_layer="0", source_handle="B")

    result, diags, welded = weld_contours([circle_a, circle_b], mode="overlapping")
    assert len(result) == 1
    result[0].assert_contiguous()
