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
