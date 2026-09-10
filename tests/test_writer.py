import math
import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.writer import write_dxf


def test_write_dxf_sets_header_and_layer(tmp_path):
    seg = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    assert doc.header["$INSUNITS"] == 4
    assert doc.dxfversion == "AC1015"
    assert "CUT" in doc.layers


def test_write_dxf_open_contour_becomes_lwpolyline(tmp_path):
    seg1 = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    seg2 = Segment(kind="line", start=(1.0, 0.0), end=(1.0, 1.0))
    contour = Contour(segments=[seg1, seg2], is_closed=False, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    entities = list(doc.modelspace())
    assert len(entities) == 1
    pl = entities[0]
    assert pl.dxftype() == "LWPOLYLINE"
    assert pl.closed is False
    assert pl.dxf.layer == "CUT"
    points = pl.get_points("xyb")
    assert len(points) == 3


def test_write_dxf_arc_segment_gets_correct_bulge(tmp_path):
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    seg2 = Segment(kind="line", start=(0.0, 1.0), end=(1.0, 0.0))
    contour = Contour(segments=[seg, seg2], is_closed=True, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    pl = list(doc.modelspace())[0]
    points = pl.get_points("xyb")
    bulge = points[0][2]
    assert bulge > 0  # CCW arc -> positive bulge
    assert math.isclose(bulge, math.tan(math.pi / 8), abs_tol=1e-6)  # 90deg sweep -> tan(theta/4)


def test_write_dxf_full_circle_contour_becomes_native_circle(tmp_path):
    center = (2.0, 3.0)
    radius = 5.0
    p_east = (center[0] + radius, center[1])
    p_west = (center[0] - radius, center[1])
    seg1 = Segment(kind="arc", start=p_east, end=p_west, center=center, radius=radius, ccw=True)
    seg2 = Segment(kind="arc", start=p_west, end=p_east, center=center, radius=radius, ccw=True)
    contour = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    entities = list(doc.modelspace())
    assert len(entities) == 1
    circle = entities[0]
    assert circle.dxftype() == "CIRCLE"
    assert math.isclose(circle.dxf.radius, 5.0, abs_tol=1e-9)
    assert math.isclose(circle.dxf.center.x, 2.0, abs_tol=1e-9)


def test_write_dxf_all_points_have_z_zero(tmp_path):
    seg = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 1.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    pl = list(doc.modelspace())[0]
    for vertex in pl:
        assert vertex[2] == 0 or len(vertex) == 3  # xyb tuples carry no z; sanity check format
    assert doc.header["$EXTMIN"][2] == 0
    assert doc.header["$EXTMAX"][2] == 0


def test_full_sweep_arc_is_written_as_two_half_arcs_not_a_straight_line(tmp_path):
    # A whole circle encoded as ONE arc segment (start == end). The naive
    # modulo bulge computation would give bulge 0 -> a zero-length line.
    seg = Segment(kind="arc", start=(11.0, 0.0), end=(11.0, 0.0),
                  center=(10.0, 0.0), radius=1.0, ccw=True)
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    pl = list(doc.modelspace())[0]
    assert pl.dxftype() == "LWPOLYLINE"
    points = pl.get_points("xyb")
    # Two half-circle arcs, each with |bulge| == 1
    arc_bulges = [b for _x, _y, b in points if b != 0.0]
    assert len(arc_bulges) == 2
    for b in arc_bulges:
        assert math.isclose(abs(b), 1.0, abs_tol=1e-9)

    from dxf_cleaner.reader import convert_polyline_entity
    from dxf_cleaner.model import contour_bbox
    rt, _ = convert_polyline_entity(pl)
    bbox = contour_bbox(rt)
    for got, want in zip(bbox, (9.0, -1.0, 11.0, 1.0)):
        assert math.isclose(got, want, abs_tol=0.05)


def test_write_dxf_raises_on_non_contiguous_contour(tmp_path):
    segs = [
        Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0)),
        Segment(kind="line", start=(5.0, 0.0), end=(6.0, 0.0)),
    ]
    contour = Contour(segments=segs, is_closed=False, source_layer="0", source_handle="1")
    import pytest
    with pytest.raises(ValueError, match="not contiguous"):
        write_dxf([contour], str(tmp_path / "out.dxf"), Config())


def test_write_dxf_skips_zero_segment_contour_without_crashing(tmp_path):
    empty = Contour(segments=[], is_closed=True, source_layer="0", source_handle="1")
    good = Contour(segments=[Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))],
                   is_closed=False, source_layer="0", source_handle="2")
    path = tmp_path / "out.dxf"
    write_dxf([empty, good], str(path), Config())
    doc = ezdxf.readfile(str(path))
    assert len(list(doc.modelspace())) == 1


def test_write_dxf_with_only_degenerate_flatten_output_does_not_crash(tmp_path):
    from dxf_cleaner.stages.flatten import _flatten_to_lines
    assert _flatten_to_lines([(1.0, 1.0)], "0", "1") is None
    assert _flatten_to_lines([], "0", "1") is None
    # Nothing to write -> empty file, no exception from contour_bbox.
    path = tmp_path / "out.dxf"
    write_dxf([], str(path), Config())
    doc = ezdxf.readfile(str(path))
    assert list(doc.modelspace()) == []


def test_non_contiguous_contour_error_names_the_offending_contour(tmp_path):
    import pytest
    from dxf_cleaner.model import Contour, Segment
    broken = Contour(
        segments=[
            Segment(kind="line", start=(0.0, 0.0), end=(10.0, 0.0)),
            Segment(kind="line", start=(10.0, 10.0), end=(0.0, 10.0)),
        ],
        is_closed=True, source_layer="CUT", source_handle="BAD1",
    )
    with pytest.raises(ValueError) as exc:
        write_dxf([broken], str(tmp_path / "out.dxf"), Config())
    message = str(exc.value)
    assert "BAD1" in message and "non-contiguous" in message
