import math
import ezdxf
import pytest
from dxf_cleaner.config import Config
from dxf_cleaner.model import contour_bbox, contour_signed_area
from dxf_cleaner.reader import read_dxf
from dxf_cleaner.writer import write_dxf


def _total_area(contours) -> float:
    return sum(abs(contour_signed_area(c)) for c in contours if c.is_closed)


def _overall_bbox(contours):
    boxes = [contour_bbox(c) for c in contours]
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


@pytest.mark.parametrize(
    "build_doc",
    [
        pytest.param(lambda doc: doc.modelspace().add_lwpolyline(
            [(0, 0), (10, 0), (10, 10), (0, 10)], format="xy", close=True
        ) and None, id="rectangle"),
        pytest.param(lambda doc: doc.modelspace().add_circle((5, 5), radius=3.0) and None, id="circle"),
        pytest.param(lambda doc: doc.modelspace().add_lwpolyline(
            [(0, 0, 0, 0, 1.0), (10, 0, 0, 0, 0.0), (10, 10, 0, 0, 0.0), (0, 10, 0, 0, 0.0)],
            format="xyseb", close=True,
        ) and None, id="polyline_with_bulge"),
        pytest.param(lambda doc: doc.modelspace().add_lwpolyline(
            [(5, 0, 0, 0, 1.0), (-5, 0, 0, 0, 0.0)], format="xyseb", close=True,
        ) and None, id="arc_plus_closing_line"),
    ],
)
def test_area_and_bbox_stable_across_roundtrip(build_doc, tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    build_doc(doc)

    first_read, second_read = _roundtrip(doc, tmp_path)

    first_area = _total_area(first_read.contours)
    second_area = _total_area(second_read.contours)
    if first_area > 0:
        assert abs(second_area - first_area) / first_area < 0.001  # <0.1%

    first_bbox = _overall_bbox(first_read.contours)
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
    assert math.isclose(first_bbox[2], 25.4, abs_tol=1e-6)  # 1 inch square -> 25.4mm wide
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
