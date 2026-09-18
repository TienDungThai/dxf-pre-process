import ezdxf
import pytest
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


def test_pipeline_welds_overlapping_squares_and_reports_weld_count(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    # two 10x10 squares overlapping diagonally (no shared/collinear edges, so
    # dedupe's collinear-overlap merge leaves them untouched and weld is the
    # only stage that can reconcile the area overlap).
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], format="xy", close=True)
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 15), (5, 15)], format="xy", close=True)
    path = tmp_path / "overlapping_squares.dxf"
    doc.saveas(path)

    config = Config()
    result = run_pipeline(str(path), config)
    assert len(result.parts) == 1
    assert result.report.info["weld_count"] == 1


def test_pipeline_simplify_reduces_node_count_only_on_touched_contours(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], format="xy", close=True)  # untouched, 4 nodes already
    msp.add_ellipse((50, 50), major_axis=(5, 0), ratio=1.0)  # circle-shaped ellipse, gets flattened+simplified
    path = tmp_path / "simplify.dxf"
    doc.saveas(path)

    config = Config()
    result = run_pipeline(str(path), config)
    untouched = next(p for p in result.parts if len(p.exterior.segments) == 4)
    assert untouched is not None


def test_weld_mode_off_disables_welding(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], format="xy", close=True)
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 15), (5, 15)], format="xy", close=True)
    path = tmp_path / "weld_off.dxf"
    doc.saveas(path)

    config = Config()
    config.weld.mode = "off"
    result = run_pipeline(str(path), config)
    assert len(result.parts) == 2


def test_simplify_remap_is_positional_not_by_duplicate_handle(tmp_path, monkeypatch):
    # Regression for the case where `snap_and_chain` gives two distinct closed
    # contours the SAME source_handle (chains inherit their handle from their
    # seed segment, so one entity split via AMBIGUOUS_JUNCTION can yield two
    # chains sharing a handle). A handle-keyed dict remap collapses to one
    # entry and can hand the wrong part the wrong geometry. Rebuilding
    # positionally (zipped against parts_to_contours' walk order) must keep
    # each part's own geometry regardless of the duplicate handle.
    import dxf_cleaner.pipeline as pipeline_module
    from dxf_cleaner.model import Segment, Contour

    def _square_contour(x0, y0, size, handle):
        x1, y1 = x0 + size, y0 + size
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        segs = [Segment(kind="line", start=pts[i], end=pts[(i + 1) % 4]) for i in range(4)]
        return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)

    # Two disjoint, differently-sized squares sharing the handle "DUP" --
    # exactly the scenario a split AMBIGUOUS_JUNCTION chain could produce.
    small = _square_contour(0, 0, 10, "DUP")
    large = _square_contour(1000, 1000, 20, "DUP")

    def fake_snap_and_chain(contours, tolerance, max_reportable_gap):
        return [small, large], []

    monkeypatch.setattr(pipeline_module, "snap_and_chain", fake_snap_and_chain)

    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_line((0, 0), (1, 1))  # placeholder so read_dxf has something to read
    path = tmp_path / "dup_handle.dxf"
    doc.saveas(path)

    config = Config()
    result = run_pipeline(str(path), config)
    assert len(result.parts) == 2

    from dxf_cleaner.model import contour_signed_area
    areas = sorted(abs(contour_signed_area(p.exterior)) for p in result.parts)
    # 10x10 -> area 100, 20x20 -> area 400: each part must keep ITS OWN
    # geometry, not both collapsing onto whichever contour the handle-keyed
    # dict happened to keep last.
    assert areas == [pytest.approx(100.0), pytest.approx(400.0)]
    assert result.report.info["weld_count"] == 0


def test_run_pipeline_on_png_input_produces_one_part_with_hole(tmp_path):
    import numpy as np
    from PIL import Image
    from dxf_cleaner.pipeline import run_pipeline
    from dxf_cleaner.config import Config

    size = 200
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[20:180, 20:180] = 0
    yy, xx = np.mgrid[0:size, 0:size]
    hole = (xx - 100) ** 2 + (yy - 100) ** 2 <= 30 ** 2
    img[hole] = 255
    path = tmp_path / "square.png"
    Image.fromarray(img, mode="RGB").save(path)

    result = run_pipeline(str(path), Config(), width_mm=160.0)

    assert len(result.parts) == 1
    assert len(result.parts[0].interiors) == 1
    assert "min_feature_width_mm" in result.report.info


def test_run_pipeline_on_png_input_simplifies_traced_contours(tmp_path):
    # Regression for: raster contours were never simplified because
    # read_raster returned flattened_handles=set(), so simplify_contours
    # (which only touches contours whose source_handle is in
    # touched_handles = flattened_handles | welded_handles) skipped every
    # raster contour regardless of config.simplify.tolerance.
    #
    # A traced CIRCLE is the wrong shape to test this with: raster.py's own
    # circle-fit collapses it to 2 clean arc segments before simplify ever
    # runs (by design, see raster.py's _fit_circle_mm), so node_count_after
    # == node_count_before == 2 regardless of whether simplify touched it.
    # A rotated square instead has straight edges that marching-squares
    # rasterizes into a pixel staircase of many near-collinear points --
    # exactly the line-simplification path this regression test targets --
    # while reliably failing the circular-fit residual check.
    import numpy as np
    import cv2
    from PIL import Image
    from dxf_cleaner.pipeline import run_pipeline
    from dxf_cleaner.config import Config

    size = 300
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    center, half_diagonal = size // 2, 130
    diamond = np.array([
        [center, center - half_diagonal],
        [center + half_diagonal, center],
        [center, center + half_diagonal],
        [center - half_diagonal, center],
    ])
    cv2.fillPoly(img, [diamond], (0, 0, 0))
    path = tmp_path / "diamond.png"
    Image.fromarray(img, mode="RGB").save(path)

    result = run_pipeline(str(path), Config(), width_mm=160.0)

    assert result.report.info["node_count_after"] < result.report.info["node_count_before"]


def test_run_pipeline_writes_preview_with_correct_hole_and_exterior_colors(tmp_path):
    # Roadmap item 2: preview rendering moved from read_raster to
    # run_pipeline (after build_hierarchy) specifically so the hole ring gets
    # drawn in its own color instead of every ring drawn as "exterior".
    import numpy as np
    from PIL import Image
    from dxf_cleaner.pipeline import run_pipeline
    from dxf_cleaner.config import Config

    size = 200
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[20:180, 20:180] = 0
    yy, xx = np.mgrid[0:size, 0:size]
    hole = (xx - 100) ** 2 + (yy - 100) ** 2 <= 30 ** 2
    img[hole] = 255
    path = tmp_path / "square.png"
    Image.fromarray(img, mode="RGB").save(path)
    preview_path = tmp_path / "square_KIEMTRA.png"

    run_pipeline(str(path), Config(), width_mm=160.0, preview_path=str(preview_path))

    assert preview_path.exists()
    preview = np.array(Image.open(preview_path).convert("RGB"))

    def _color_present_near(color, row, col, radius=4):
        window = preview[row - radius:row + radius + 1, col - radius:col + radius + 1]
        return np.any(np.all(window == color, axis=-1))

    # render_preview's own contract: exterior boundary -> RGB (0, 140, 0)
    # (green), hole boundary -> RGB (0, 120, 200) (blue). The outer square
    # edge sits near row=20 (img[20:180, ...] = 0); the hole (radius 30,
    # centered at (100, 100)) has its top edge near row=70.
    assert _color_present_near((0, 140, 0), row=20, col=100), "exterior boundary not drawn in green"
    assert _color_present_near((0, 120, 200), row=70, col=100), "hole boundary not drawn in blue"


def test_run_pipeline_warns_when_weld_drops_boundaries_from_preview(tmp_path):
    # Regression: weld_contours re-emits a union under a NEW source_handle
    # ("WELD_..."), so a welded raster shape has no entry in
    # rings_px_by_handle and its outline is silently missing from the
    # mandatory _KIEMTRA.png check image -- while the shape itself is still
    # very much present in the cleaned DXF output. weld.mode="all" unions
    # every closed contour unconditionally, so even a simple square-with-hole
    # image (2 closed contours: exterior + hole) triggers it.
    import numpy as np
    from PIL import Image
    from dxf_cleaner.pipeline import run_pipeline
    from dxf_cleaner.config import Config

    size = 200
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[20:180, 20:180] = 0
    yy, xx = np.mgrid[0:size, 0:size]
    hole = (xx - 100) ** 2 + (yy - 100) ** 2 <= 30 ** 2
    img[hole] = 255
    path = tmp_path / "square.png"
    Image.fromarray(img, mode="RGB").save(path)
    preview_path = tmp_path / "square_KIEMTRA.png"

    config = Config(weld={"mode": "all"})
    result = run_pipeline(str(path), config, width_mm=160.0, preview_path=str(preview_path))

    assert preview_path.exists()
    codes = [d.code for d in result.diagnostics]
    assert "PREVIEW_INCOMPLETE" in codes
    assert result.report.level == "warning"


def test_run_pipeline_on_dxf_input_still_works_without_new_kwargs(tmp_path):
    import ezdxf
    from dxf_cleaner.pipeline import run_pipeline
    from dxf_cleaner.config import Config

    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    path = tmp_path / "square.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())

    assert len(result.parts) == 1
    assert "min_feature_width_mm" not in result.report.info
