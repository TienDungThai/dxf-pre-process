from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.despeckle import despeckle_contours


def _square(side, handle="H"):
    p = [(0.0, 0.0), (side, 0.0), (side, side), (0.0, side)]
    segs = [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


def test_tiny_square_below_area_threshold_is_removed():
    tiny = _square(0.1, handle="TINY")  # area 0.01mm2 < default min_area 0.1
    result, diagnostics = despeckle_contours([tiny], min_perimeter=0.5, min_area=0.1)
    assert result == []
    assert any(d.code == "DESPECKLED" and d.handle == "TINY" for d in diagnostics)


def test_normal_square_is_kept():
    normal = _square(10.0, handle="NORMAL")
    result, diagnostics = despeckle_contours([normal], min_perimeter=0.5, min_area=0.1)
    assert len(result) == 1
    assert diagnostics == []


def test_open_contours_are_never_despeckled():
    tiny_open = Contour(
        segments=[Segment(kind="line", start=(0.0, 0.0), end=(0.01, 0.0))],
        is_closed=False, source_layer="0", source_handle="OPEN",
    )
    result, diagnostics = despeckle_contours([tiny_open], min_perimeter=0.5, min_area=0.1)
    assert result == [tiny_open]
    assert diagnostics == []


def test_thin_sliver_below_perimeter_threshold_is_removed_even_with_zero_area():
    # a degenerate near-zero-width closed sliver: large enough area might round to
    # ~0, but perimeter is the deciding factor here.
    segs = [
        Segment(kind="line", start=(0.0, 0.0), end=(0.1, 0.0)),
        Segment(kind="line", start=(0.1, 0.0), end=(0.1, 0.001)),
        Segment(kind="line", start=(0.1, 0.001), end=(0.0, 0.001)),
        Segment(kind="line", start=(0.0, 0.001), end=(0.0, 0.0)),
    ]
    sliver = Contour(segments=segs, is_closed=True, source_layer="0", source_handle="SLIVER")
    result, diagnostics = despeckle_contours([sliver], min_perimeter=0.5, min_area=0.1)
    assert result == []
    assert any(d.code == "DESPECKLED" for d in diagnostics)
