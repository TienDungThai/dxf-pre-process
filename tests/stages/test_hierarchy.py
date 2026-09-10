import math
from shapely import make_valid
from dxf_cleaner.model import Segment, Contour, contour_signed_area
from dxf_cleaner.stages.hierarchy import build_hierarchy


def _square(x0, y0, side, ccw=True, handle="H"):
    if ccw:
        p = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    else:
        p = [(x0, y0), (x0, y0 + side), (x0 + side, y0 + side), (x0 + side, y0)]
    segs = [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


def test_single_square_becomes_one_part_with_no_interiors():
    sq = _square(0, 0, 10, handle="OUTER")
    parts, diagnostics = build_hierarchy([sq])
    assert len(parts) == 1
    assert parts[0].interiors == []
    assert contour_signed_area(parts[0].exterior) > 0  # CCW


def test_nested_hole_is_attached_as_interior_and_reoriented_cw():
    outer = _square(0, 0, 10, handle="OUTER")
    hole = _square(2, 2, 3, handle="HOLE")  # entirely inside outer, drawn CCW (wrong for a hole)
    parts, diagnostics = build_hierarchy([outer, hole])
    assert len(parts) == 1
    assert len(parts[0].interiors) == 1
    assert contour_signed_area(parts[0].interiors[0]) < 0  # reoriented CW


def test_island_inside_hole_is_its_own_part_not_nested():
    outer = _square(0, 0, 20, handle="OUTER")
    hole = _square(5, 5, 10, handle="HOLE")
    island = _square(7, 7, 2, handle="ISLAND")
    parts, diagnostics = build_hierarchy([outer, hole, island])
    assert len(parts) == 2  # OUTER-with-HOLE, and ISLAND standalone
    outer_part = next(p for p in parts if len(p.interiors) == 1)
    island_part = next(p for p in parts if len(p.interiors) == 0)
    assert island_part.exterior.source_handle == "ISLAND"
    assert contour_signed_area(island_part.exterior) > 0  # CCW, independent exterior


def test_open_contour_is_skipped_with_diagnostic():
    open_c = Contour(
        segments=[Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))],
        is_closed=False, source_layer="0", source_handle="OPEN",
    )
    parts, diagnostics = build_hierarchy([open_c])
    assert parts == []
    assert any(d.code == "OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY" for d in diagnostics)


def test_self_intersecting_bowtie_is_fixed_or_reported():
    # a bowtie (figure-8) polygon: self-intersecting, invalid until make_valid runs.
    bowtie = Contour(
        segments=[
            Segment(kind="line", start=(0.0, 0.0), end=(10.0, 10.0)),
            Segment(kind="line", start=(10.0, 10.0), end=(10.0, 0.0)),
            Segment(kind="line", start=(10.0, 0.0), end=(0.0, 10.0)),
            Segment(kind="line", start=(0.0, 10.0), end=(0.0, 0.0)),
        ],
        is_closed=True, source_layer="0", source_handle="BOWTIE",
    )
    parts, diagnostics = build_hierarchy([bowtie])
    codes = {d.code for d in diagnostics}
    assert "INVALID_POLYGON_FIXED" in codes or "POLYGON_UNRECOVERABLE" in codes
