import pytest
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.dedupe import dedupe_contours


def _contour(segments, closed=False, layer="0", handle="H"):
    return Contour(segments=segments, is_closed=closed, source_layer=layer, source_handle=handle)


def test_fully_duplicate_standalone_lines_are_deduped():
    # duplicate_lines.dxf equivalent: two entities drawing the exact same line,
    # one reversed (a common Corel stroke+fill export artifact).
    c1 = _contour([Segment(kind="line", start=(0.0, 0.0), end=(5.0, 0.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(5.0, 0.0), end=(0.0, 0.0))], handle="B")
    result, diagnostics = dedupe_contours([c1, c2], merge_common_edges=False)
    assert len(result) == 1
    assert any(d.code == "DUPLICATE_SEGMENT_REMOVED" for d in diagnostics)


def test_shared_edge_between_two_rectangles_kept_by_default():
    # two adjacent 4-segment rectangles sharing one full edge (x=10 from y=0 to y=10).
    left = _contour([
        Segment(kind="line", start=(0.0, 0.0), end=(10.0, 0.0)),
        Segment(kind="line", start=(10.0, 0.0), end=(10.0, 10.0)),
        Segment(kind="line", start=(10.0, 10.0), end=(0.0, 10.0)),
        Segment(kind="line", start=(0.0, 10.0), end=(0.0, 0.0)),
    ], closed=True, handle="LEFT")
    right = _contour([
        Segment(kind="line", start=(10.0, 0.0), end=(20.0, 0.0)),
        Segment(kind="line", start=(20.0, 0.0), end=(20.0, 10.0)),
        Segment(kind="line", start=(20.0, 10.0), end=(10.0, 10.0)),
        Segment(kind="line", start=(10.0, 10.0), end=(10.0, 0.0)),  # shared edge, reversed direction
    ], closed=True, handle="RIGHT")
    result, diagnostics = dedupe_contours([left, right], merge_common_edges=False)
    assert sum(len(c.segments) for c in result) == 8  # nothing removed
    assert any(d.code == "COMMON_EDGE" for d in diagnostics)


def test_shared_edge_removed_when_merge_common_edges_enabled():
    left = _contour([
        Segment(kind="line", start=(0.0, 0.0), end=(10.0, 0.0)),
        Segment(kind="line", start=(10.0, 0.0), end=(10.0, 10.0)),
        Segment(kind="line", start=(10.0, 10.0), end=(0.0, 10.0)),
        Segment(kind="line", start=(0.0, 10.0), end=(0.0, 0.0)),
    ], closed=True, handle="LEFT")
    right = _contour([
        Segment(kind="line", start=(10.0, 10.0), end=(10.0, 0.0)),  # only the shared edge, for simplicity
    ], closed=False, handle="RIGHT")
    result, diagnostics = dedupe_contours([left, right], merge_common_edges=True)
    assert sum(len(c.segments) for c in result) == 4  # right's only segment removed, right contour dropped entirely
    assert len(result) == 1


def test_partially_overlapping_collinear_lines_are_merged():
    # two overlapping horizontal segments on y=0: [0,6] and [4,10] -> merged to [0,10]
    c1 = _contour([Segment(kind="line", start=(0.0, 0.0), end=(6.0, 0.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(4.0, 0.0), end=(10.0, 0.0))], handle="B")
    result, diagnostics = dedupe_contours([c1, c2], merge_common_edges=False)
    assert len(result) == 1
    seg = result[0].segments[0]
    assert {seg.start, seg.end} == {(0.0, 0.0), (10.0, 0.0)}
    assert any(d.code == "OVERLAPPING_SEGMENTS_MERGED" for d in diagnostics)


def test_non_overlapping_collinear_lines_are_left_alone():
    c1 = _contour([Segment(kind="line", start=(0.0, 0.0), end=(2.0, 0.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(5.0, 0.0), end=(7.0, 0.0))], handle="B")
    result, diagnostics = dedupe_contours([c1, c2], merge_common_edges=False)
    assert len(result) == 2
    assert not any(d.code == "OVERLAPPING_SEGMENTS_MERGED" for d in diagnostics)


def test_overlapping_collinear_lines_off_origin_keep_their_offset():
    # Regression: the merged segment used to be reconstructed on the parallel
    # line through the ORIGIN, silently moving y=5 geometry to y=0.
    c1 = _contour([Segment(kind="line", start=(0.0, 5.0), end=(6.0, 5.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(4.0, 5.0), end=(10.0, 5.0))], handle="B")
    result, diagnostics = dedupe_contours([c1, c2], merge_common_edges=False)
    assert len(result) == 1
    seg = result[0].segments[0]
    assert {seg.start, seg.end} == {(0.0, 5.0), (10.0, 5.0)}
    assert any(d.code == "OVERLAPPING_SEGMENTS_MERGED" for d in diagnostics)


def test_overlapping_collinear_diagonal_lines_off_origin():
    # y = x + 4: (0,4)-(6,10) and (4,8)-(10,14) -> (0,4)-(10,14)
    c1 = _contour([Segment(kind="line", start=(0.0, 4.0), end=(6.0, 10.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(4.0, 8.0), end=(10.0, 14.0))], handle="B")
    result, _ = dedupe_contours([c1, c2], merge_common_edges=False)
    seg = result[0].segments[0]
    for p in (seg.start, seg.end):
        assert abs(p[1] - (p[0] + 4.0)) < 1e-6
    assert min(seg.start[0], seg.end[0]) == pytest.approx(0.0)
    assert max(seg.start[0], seg.end[0]) == pytest.approx(10.0)


def _square(x0, y0, side, handle):
    p = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    return _contour(
        [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)],
        closed=True, handle=handle,
    )


def test_merge_common_edges_marks_the_broken_survivor_open():
    # Two 100x100 squares sharing the x=100 edge. merge_common_edges removes the
    # shared edge from one of them, which breaks its ring -- it must not stay
    # is_closed=True (that violates Contour's contiguity invariant).
    left = _square(0, 0, 100, "LEFT")
    right = _square(100, 0, 100, "RIGHT")
    result, diagnostics = dedupe_contours([left, right], merge_common_edges=True)
    opened = [c for c in result if len(c.segments) == 3]
    assert len(opened) == 1
    assert opened[0].is_closed is False
    assert any(d.code == "CONTOUR_OPENED_BY_DEDUPE" for d in diagnostics)
    for c in result:
        c.assert_contiguous()


def _rect(x0, y0, x1, y1, handle):
    p = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return _contour(
        [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)],
        closed=True, handle=handle,
    )


def test_overlap_merge_marks_the_extended_survivor_open():
    # Two abutting rectangles whose border edges are collinear on x=100 but only
    # PARTIALLY overlap (LEFT spans y 0..80, RIGHT spans y 20..100), under
    # DEFAULT config. The case-(b) overlap merge replaces LEFT's edge with the
    # union interval y 0..100 -- extending its endpoint past where LEFT's next
    # ring segment starts (100, 80). LEFT's segment COUNT is unchanged, so a
    # contiguity re-check gated on "lost a segment" misses it; it must not stay
    # is_closed=True while actually being broken.
    left = _rect(0, 0, 100, 80, "LEFT")
    right = _rect(100, 20, 200, 100, "RIGHT")
    result, diagnostics = dedupe_contours([left, right], merge_common_edges=False)

    assert any(d.code == "OVERLAPPING_SEGMENTS_MERGED" for d in diagnostics)
    by_handle = {c.source_handle: c for c in result}
    assert len(by_handle["LEFT"].segments) == 4  # nothing removed from LEFT
    assert by_handle["LEFT"].is_closed is False
    assert any(
        d.code == "CONTOUR_OPENED_BY_DEDUPE" and d.handle == "LEFT" for d in diagnostics
    )


def test_untouched_closed_contour_stays_closed():
    # Guard against the generalized re-check opening contours it never touched.
    lone = _rect(0, 0, 10, 10, "LONE")
    far = _rect(100, 100, 110, 110, "FAR")
    result, diagnostics = dedupe_contours([lone, far], merge_common_edges=False)
    assert all(c.is_closed for c in result)
    assert not any(d.code == "CONTOUR_OPENED_BY_DEDUPE" for d in diagnostics)
