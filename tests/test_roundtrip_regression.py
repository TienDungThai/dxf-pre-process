import math
import ezdxf
import pytest
from ezdxf.math import Vec3, rational_bspline_from_arc
from dxf_cleaner.config import Config
from dxf_cleaner.model import contour_bbox, contour_signed_area
from dxf_cleaner.reader import read_dxf
from dxf_cleaner.writer import write_dxf


# Ground-truth comparisons discretize arcs finely so the polygonal
# approximation error stays far below the assertion tolerances; the
# first-vs-second stability checks use the default tolerance.
_FINE_TOL = 1e-4


def _total_area(contours, arc_tolerance: float = 0.02) -> float:
    return sum(abs(contour_signed_area(c, arc_tolerance)) for c in contours if c.is_closed)


def _overall_bbox(contours, arc_tolerance: float = 0.02):
    boxes = [contour_bbox(c, arc_tolerance) for c in contours]
    minxs, minys, maxxs, maxys = zip(*boxes)
    return (min(minxs), min(minys), max(maxxs), max(maxys))


def _roundtrip(doc, tmp_path, config: Config = Config()):
    input_path = tmp_path / "input.dxf"
    doc.saveas(input_path)

    first_read = read_dxf(str(input_path), config)

    output_path = tmp_path / "output.dxf"
    write_dxf(first_read.contours, str(output_path), config)

    second_read = read_dxf(str(output_path), config)
    return first_read, second_read


# --- fixture builders -------------------------------------------------------

def _build_rectangle(doc):
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], format="xy", close=True)


def _build_circle(doc):
    doc.modelspace().add_circle((5, 5), radius=3.0)


def _build_polyline_with_bulge(doc):
    # Square 10x10 whose bottom edge is replaced by a CCW semicircle (bulge=+1,
    # radius 5, centre (5,0)) that bows DOWN to y=-5.
    doc.modelspace().add_lwpolyline(
        [(0, 0, 0, 0, 1.0), (10, 0, 0, 0, 0.0), (10, 10, 0, 0, 0.0), (0, 10, 0, 0, 0.0)],
        format="xyseb", close=True,
    )


def _build_arc_plus_closing_line(doc):
    # CCW semicircle (radius 5, centre origin) from (5,0) to (-5,0) bowing UP,
    # closed by the straight diameter.
    doc.modelspace().add_lwpolyline(
        [(5, 0, 0, 0, 1.0), (-5, 0, 0, 0, 0.0)], format="xyseb", close=True,
    )


def _build_ellipse_arc_off_origin(doc):
    # Circular ELLIPSE arc, centre (100,0), radius 1, 90deg -> 270deg. CCW from
    # 90 to 270 passes through 180, so the arc lies on the LEFT half:
    # bbox = (99, -1, 100, 1). Regression guard for the winding-about-the-origin
    # bug, which reported the mirror arc (bbox (100,-1,101,1)).
    doc.modelspace().add_ellipse(
        center=(100, 0), major_axis=(1, 0), ratio=1.0,
        start_param=math.pi / 2, end_param=3 * math.pi / 2,
    )


def _build_linear_spline(doc):
    # Degree-1 (piecewise-linear) spline: genuinely non-circular, so it must
    # round-trip as line segments with an exact analytic bbox.
    spline = doc.modelspace().add_spline(degree=1)
    spline.control_points = [(0, 0), (10, 0), (10, 10), (20, 10)]


def _build_spline_full_circle(doc):
    # Rational B-spline that exactly traces a full circle, centre (20,20) r=5.
    tool = rational_bspline_from_arc(center=Vec3(20, 20, 0), radius=5.0,
                                    start_angle=0, end_angle=360)
    doc.modelspace().add_spline().apply_construction_tool(tool)


# (id, builder, expected_total_closed_area, expected_bbox)
_FIXTURES = [
    ("rectangle", _build_rectangle, 100.0, (0.0, 0.0, 10.0, 10.0)),
    ("circle", _build_circle, math.pi * 3.0 ** 2, (2.0, 2.0, 8.0, 8.0)),
    ("polyline_with_bulge", _build_polyline_with_bulge,
     100.0 + 0.5 * math.pi * 5.0 ** 2, (0.0, -5.0, 10.0, 10.0)),
    ("arc_plus_closing_line", _build_arc_plus_closing_line,
     0.5 * math.pi * 5.0 ** 2, (-5.0, 0.0, 5.0, 5.0)),
    ("ellipse_arc_off_origin", _build_ellipse_arc_off_origin,
     0.0, (99.0, -1.0, 100.0, 1.0)),
    ("linear_spline", _build_linear_spline, 0.0, (0.0, 0.0, 20.0, 10.0)),
    ("spline_full_circle", _build_spline_full_circle,
     math.pi * 5.0 ** 2, (15.0, 15.0, 25.0, 25.0)),
]


@pytest.mark.parametrize(
    "build_doc,expected_area,expected_bbox",
    [pytest.param(b, a, bb, id=i) for i, b, a, bb in _FIXTURES],
)
def test_area_and_bbox_match_ground_truth_and_are_stable_across_roundtrip(
    build_doc, expected_area, expected_bbox, tmp_path
):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    build_doc(doc)

    first_read, second_read = _roundtrip(doc, tmp_path)

    first_area = _total_area(first_read.contours)
    second_area = _total_area(second_read.contours)
    first_area_fine = _total_area(first_read.contours, _FINE_TOL)

    # (1) Ground truth: the FIRST read must match the value computed from the
    # fixture's own construction parameters. Without this, a reader-side
    # geometry bug produces the same wrong answer on both reads and the
    # first-vs-second comparison below passes vacuously.
    if expected_area > 0:
        assert abs(first_area_fine - expected_area) / expected_area < 0.002
    else:
        assert first_area == 0.0

    first_bbox_fine = _overall_bbox(first_read.contours, _FINE_TOL)
    for got, want in zip(first_bbox_fine, expected_bbox):
        assert math.isclose(got, want, abs_tol=0.02), f"{first_bbox_fine} != {expected_bbox}"

    first_bbox = _overall_bbox(first_read.contours)

    # (2) Stability: write-side regressions still show up as first-vs-second drift.
    if first_area > 0:
        assert abs(second_area - first_area) / first_area < 0.001  # <0.1%

    second_bbox = _overall_bbox(second_read.contours)
    for a, b in zip(first_bbox, second_bbox):
        assert math.isclose(a, b, abs_tol=0.02)  # within chord tolerance


def test_inch_file_roundtrip_preserves_mm_dimensions(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 1  # inch
    doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], format="xy", close=True)

    first_read, second_read = _roundtrip(doc, tmp_path)

    assert first_read.unit_scale == 25.4
    first_bbox = _overall_bbox(first_read.contours)
    # Ground truth: a 1-inch square is 25.4mm on a side.
    for got, want in zip(first_bbox, (0.0, 0.0, 25.4, 25.4)):
        assert math.isclose(got, want, abs_tol=1e-6)
    assert math.isclose(_total_area(first_read.contours), 25.4 ** 2, rel_tol=1e-9)
    second_bbox = _overall_bbox(second_read.contours)
    for a, b in zip(first_bbox, second_bbox):
        assert math.isclose(a, b, abs_tol=0.02)


def test_circle_arc_count_does_not_explode_after_roundtrip(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_circle((0, 0), radius=10.0)

    first_read, second_read = _roundtrip(doc, tmp_path)

    # A circle must round-trip as a 2-arc contour both times (acceptance criterion #5:
    # node count for arc-bearing contours must not grow >20% — here it must not grow at all).
    assert len(first_read.contours[0].segments) == 2
    assert len(second_read.contours[0].segments) == 2
    assert all(seg.kind == "arc" for seg in second_read.contours[0].segments)


def test_spline_full_circle_arc_count_does_not_explode_after_roundtrip(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    _build_spline_full_circle(doc)

    first_read, second_read = _roundtrip(doc, tmp_path)

    # Circular re-detection must collapse the flattened spline to 2 arcs and
    # keep it at 2 across the write/read cycle.
    assert len(first_read.contours[0].segments) == 2
    assert all(seg.kind == "arc" for seg in first_read.contours[0].segments)
    assert len(second_read.contours[0].segments) == 2
    assert all(seg.kind == "arc" for seg in second_read.contours[0].segments)


def test_ellipse_arc_off_origin_is_not_mirrored_after_roundtrip(tmp_path):
    """The mirror-arc bug shifted this arc by 1mm; assert the true bbox directly."""
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    _build_ellipse_arc_off_origin(doc)

    first_read, second_read = _roundtrip(doc, tmp_path)

    for read in (first_read, second_read):
        bbox = _overall_bbox(read.contours)
        for got, want in zip(bbox, (99.0, -1.0, 100.0, 1.0)):
            assert math.isclose(got, want, abs_tol=0.02), bbox


def test_linear_spline_roundtrips_as_line_segments(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    _build_linear_spline(doc)

    first_read, second_read = _roundtrip(doc, tmp_path)

    assert all(seg.kind == "line" for seg in first_read.contours[0].segments)
    assert all(seg.kind == "line" for seg in second_read.contours[0].segments)
