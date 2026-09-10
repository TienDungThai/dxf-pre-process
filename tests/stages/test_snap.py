import math
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.snap import snap_and_chain


def _open_contour(segments, layer="0", handle="H"):
    return Contour(segments=segments, is_closed=False, source_layer=layer, source_handle=handle)


def test_two_lines_with_small_gap_are_chained_into_one_open_contour():
    # two separate LINE entities that should form one continuous open polyline,
    # but their shared point is 0.03mm apart (a common Corel rounding artifact).
    c1 = _open_contour([Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))], handle="A")
    c2 = _open_contour([Segment(kind="line", start=(1.03, 0.0), end=(2.0, 0.0))], handle="B")
    result, diagnostics = snap_and_chain([c1, c2], tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 1
    assert len(result[0].segments) == 2
    assert result[0].is_closed is False
    # the joined point must be a single canonical location, not either original value
    joined = result[0].segments[0].end
    assert result[0].segments[1].start == joined
    assert not any(d.code == "OPEN_GAP" for d in diagnostics)


def test_four_lines_with_small_gaps_close_into_one_closed_contour():
    # an "open rectangle" made of 4 separate LINE entities, each corner off by 0.02mm.
    p = [(0.0, 0.0), (10.02, 0.0), (10.0, 10.02), (-0.02, 10.0)]
    segs = [
        _open_contour([Segment(kind="line", start=p[0], end=p[1])], handle="A"),
        _open_contour([Segment(kind="line", start=p[1], end=p[2])], handle="B"),
        _open_contour([Segment(kind="line", start=p[2], end=p[3])], handle="C"),
        _open_contour([Segment(kind="line", start=(p[3][0], p[3][1]), end=(0.02, 0.0))], handle="D"),
    ]
    result, diagnostics = snap_and_chain(segs, tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 1
    assert result[0].is_closed is True
    assert len(result[0].segments) == 4
    result[0].assert_contiguous()  # no ValueError


def test_gap_within_reportable_range_is_reported_but_not_joined():
    c1 = _open_contour([Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))], handle="A")
    c2 = _open_contour([Segment(kind="line", start=(1.5, 0.0), end=(2.5, 0.0))], handle="B")
    result, diagnostics = snap_and_chain([c1, c2], tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 2  # not joined, 0.5mm > 0.05mm tolerance
    gap_diags = [d for d in diagnostics if d.code == "OPEN_GAP"]
    assert len(gap_diags) == 1
    assert "0.5" in gap_diags[0].message


def test_gap_beyond_max_reportable_gap_is_not_reported():
    c1 = _open_contour([Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))], handle="A")
    c2 = _open_contour([Segment(kind="line", start=(10.0, 0.0), end=(11.0, 0.0))], handle="B")
    result, diagnostics = snap_and_chain([c1, c2], tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 2
    assert not any(d.code == "OPEN_GAP" for d in diagnostics)


def test_t_junction_reports_ambiguous_and_still_chains_two_of_three():
    # three lines meeting at the origin: this is a branch, not a simple chain.
    a = Segment(kind="line", start=(-1.0, 0.0), end=(0.0, 0.0))
    b = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    c = Segment(kind="line", start=(0.0, 0.0), end=(0.0, 1.0))
    result, diagnostics = snap_and_chain(
        [_open_contour([a], handle="A"), _open_contour([b], handle="B"), _open_contour([c], handle="C")],
        tolerance=0.05, max_reportable_gap=2.0,
    )
    assert any(d.code == "AMBIGUOUS_JUNCTION" for d in diagnostics)
    assert sum(len(c.segments) for c in result) == 3  # no segment lost
    assert len(result) == 2  # one 2-segment chain + one leftover 1-segment contour
