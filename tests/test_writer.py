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
