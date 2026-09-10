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


def test_nested_contour_containment_is_not_treated_as_overlap():
    # A hole/island fully contained within another closed contour (e.g. an
    # outer part boundary and its interior hole, later sorted out by the
    # hierarchy stage) has a large-area intersection but neither polygon
    # overlaps the other in the geometric sense -- weld must leave both
    # untouched rather than merging away the containment relationship.
    outer = _square(0, 0, 20, handle="OUTER")
    inner = _square(5, 5, 10, handle="INNER")
    result, diags, welded = weld_contours([outer, inner], mode="overlapping")
    assert len(result) == 2
    assert {c.source_handle for c in result} == {"OUTER", "INNER"}
    assert welded == set()
    assert diags == []


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


def test_unrecoverable_polygon_is_passed_through_not_dropped(monkeypatch):
    # _to_valid_polygon returns None when a closed contour's polygon is
    # unrecoverable even after make_valid (e.g. a self-intersecting contour
    # that collapses to a GeometryCollection of only lines/points). Real-world
    # inputs that trigger this are hard to construct reliably because shapely's
    # make_valid is robust, so we make the specific contour "BAD" unrecoverable
    # by wrapping _to_valid_polygon and forcing it to return None only for that
    # contour's handle, leaving the real logic untouched for every other input.
    # This directly exercises the weld_contours branch under test: does the
    # contour get silently dropped, or passed through to `result` unwelded?
    import dxf_cleaner.stages.weld as weld_mod

    bad = _square(0, 0, 10, handle="BAD")
    good = _square(50, 50, 10, handle="GOOD")

    orig_to_valid_polygon = weld_mod._to_valid_polygon

    def fake_to_valid_polygon(contour, arc_tolerance, diagnostics):
        if contour.source_handle == "BAD":
            diagnostics.append(weld_mod.Diagnostic(
                code="POLYGON_UNRECOVERABLE",
                message="Could not recover a usable polygon for weld",
                handle=contour.source_handle,
            ))
            return None
        return orig_to_valid_polygon(contour, arc_tolerance, diagnostics)

    monkeypatch.setattr(weld_mod, "_to_valid_polygon", fake_to_valid_polygon)

    result, diags, welded = weld_contours([bad, good], mode="overlapping")

    codes = {d.code for d in diags}
    assert "POLYGON_UNRECOVERABLE" in codes

    handles = {c.source_handle for c in result}
    assert "BAD" in handles
    bad_result = next(c for c in result if c.source_handle == "BAD")
    assert bad_result == bad
    assert "GOOD" in handles


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


def test_mode_all_warns_when_a_nested_hole_is_lost_in_the_union():
    # In "all" mode every closed contour in the cluster gets unioned together,
    # including a hole contour fully contained by its own parent's exterior --
    # unary_union of a containing polygon and a contained one just returns the
    # containing polygon, silently deleting the hole. HOLES_LOST_IN_WELD_ALL
    # must fire, naming the absorbed contour's handle.
    outer = _square(0, 0, 20, handle="OUTER")
    hole = _square(5, 5, 10, handle="HOLE")
    result, diags, welded = weld_contours([outer, hole], mode="all")
    assert len(result) == 1
    lost = [d for d in diags if d.code == "HOLES_LOST_IN_WELD_ALL"]
    assert len(lost) == 1
    assert lost[0].handle == "HOLE"


def test_mode_overlapping_does_not_warn_about_nested_containment():
    # The same nested outer/hole pair in "overlapping" mode is left untouched
    # (containment isn't "genuine overlap"), so no data is lost and no
    # HOLES_LOST_IN_WELD_ALL diagnostic should be emitted.
    outer = _square(0, 0, 20, handle="OUTER")
    hole = _square(5, 5, 10, handle="HOLE")
    result, diags, welded = weld_contours([outer, hole], mode="overlapping")
    assert len(result) == 2
    assert not any(d.code == "HOLES_LOST_IN_WELD_ALL" for d in diags)


def test_mode_all_does_not_warn_for_independent_non_nested_contours():
    # Two disjoint squares welded in "all" mode still merge (mode "all" unions
    # everything regardless of overlap), but neither is contained by the
    # other, so nothing is silently lost and no warning should fire.
    a = _square(0, 0, 10, handle="A")
    b = _square(100, 100, 10, handle="B")
    result, diags, welded = weld_contours([a, b], mode="all")
    assert not any(d.code == "HOLES_LOST_IN_WELD_ALL" for d in diags)
