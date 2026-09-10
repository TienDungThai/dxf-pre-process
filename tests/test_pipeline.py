import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.pipeline import run_pipeline, parts_to_contours, write_pipeline_result
from dxf_cleaner.reader import read_dxf


def test_open_rectangle_of_four_lines_becomes_one_clean_closed_part(tmp_path):
    # spec's open_contour.dxf equivalent: 4 separate LINE entities forming a
    # rectangle whose corners are 0.02mm apart (snap should close it).
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    p = [(0.0, 0.0), (10.02, 0.0), (10.0, 10.02), (-0.02, 10.0)]
    msp.add_line(p[0], p[1])
    msp.add_line(p[1], p[2])
    msp.add_line(p[2], p[3])
    msp.add_line(p[3], (0.02, 0.0))
    path = tmp_path / "open_rectangle.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())
    assert len(result.parts) == 1
    assert result.parts[0].interiors == []
    assert result.report.level == "ok"


def test_duplicate_lines_are_deduped_end_to_end(tmp_path):
    # spec's duplicate_lines.dxf equivalent.
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_line((0, 0), (5, 0))
    msp.add_line((5, 0), (0, 0))  # exact duplicate, reversed
    path = tmp_path / "duplicate_lines.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())
    total_segments = sum(len(p.exterior.segments) + sum(len(h.segments) for h in p.interiors) for p in result.parts)
    # both lines are open (never close into a Part) — this test only checks
    # dedupe ran: only one DUPLICATE_SEGMENT_REMOVED diagnostic should exist.
    assert any(d.code == "DUPLICATE_SEGMENT_REMOVED" for d in result.diagnostics)


def test_nested_holes_end_to_end(tmp_path):
    # spec's nested_holes.dxf equivalent: outer square with a hole, and a small
    # island inside that hole.
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], format="xy", close=True)
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 15), (5, 15)], format="xy", close=True)
    msp.add_lwpolyline([(8, 8), (10, 8), (10, 10), (8, 10)], format="xy", close=True)
    path = tmp_path / "nested_holes.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())
    assert len(result.parts) == 2
    outer_part = next(p for p in result.parts if p.interiors)
    assert len(outer_part.interiors) == 1


def test_parts_to_contours_flattens_exterior_and_interiors():
    import ezdxf
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], format="xy", close=True)
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 15), (5, 15)], format="xy", close=True)
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".dxf")
    os.close(fd)
    doc.saveas(path)
    result = run_pipeline(path, Config())
    os.remove(path)
    contours = parts_to_contours(result.parts)
    assert len(contours) == 2  # 1 exterior + 1 interior


def test_write_pipeline_result_produces_a_readable_dxf(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_circle((5, 5), radius=3.0)
    in_path = tmp_path / "in.dxf"
    doc.saveas(in_path)

    result = run_pipeline(str(in_path), Config())
    out_path = tmp_path / "out.dxf"
    write_pipeline_result(result, str(out_path), Config())

    reread = read_dxf(str(out_path), Config())
    assert len(reread.contours) == 1


def test_shared_edge_merge_breaking_a_square_is_reported_critical(tmp_path):
    # Two 100x100 squares sharing the x=100 edge, with merge_common_edges on.
    # The shared edge is removed from one square, opening it -- that must
    # surface as a critical report, not a silent "warning" plus a writer crash.
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], format="xy", close=True)
    msp.add_lwpolyline([(100, 0), (200, 0), (200, 100), (100, 100)], format="xy", close=True)
    path = tmp_path / "common_edge.dxf"
    doc.saveas(path)

    config = Config()
    config.dedupe.merge_common_edges = True
    result = run_pipeline(str(path), config)

    assert any(d.code == "CONTOUR_OPENED_BY_DEDUPE" for d in result.diagnostics)
    assert any(d.code == "OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY" for d in result.diagnostics)
    assert result.report.level == "critical"

    # the opened contour never reaches the writer, so writing still works
    out_path = tmp_path / "out.dxf"
    write_pipeline_result(result, str(out_path), config)
    assert out_path.exists()


def test_partially_overlapping_collinear_border_edge_is_reported_critical(tmp_path):
    # Two abutting rectangles whose x=100 border edges are collinear and only
    # PARTIALLY overlapping. The default-config overlap merge extends one
    # rectangle's edge to the union interval, breaking its ring -- that must
    # surface as a critical report, not a silent pass or an unattributed crash.
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 80), (0, 80)], format="xy", close=True)
    msp.add_lwpolyline([(100, 20), (200, 20), (200, 100), (100, 100)], format="xy", close=True)
    path = tmp_path / "overlap_edge.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())

    assert any(d.code == "CONTOUR_OPENED_BY_DEDUPE" for d in result.diagnostics)
    assert any(d.code == "OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY" for d in result.diagnostics)
    assert result.report.level == "critical"

    out_path = tmp_path / "out.dxf"
    write_pipeline_result(result, str(out_path), config=Config())
    assert out_path.exists()
