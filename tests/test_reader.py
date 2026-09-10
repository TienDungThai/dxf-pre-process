import math

from dxf_cleaner.config import Config
from dxf_cleaner.reader import determine_unit_scale, convert_simple_entity
from dxf_cleaner.reader import convert_polyline_entity


def test_insunits_inch_gives_scale_25_4(new_doc):
    new_doc.header["$INSUNITS"] = 1
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 25.4
    assert diag is None


def test_insunits_mm_gives_scale_1(new_doc):
    new_doc.header["$INSUNITS"] = 4
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 1.0
    assert diag is None


def test_insunits_unset_falls_back_to_assumed_unit_mm_with_warning(new_doc):
    new_doc.header["$INSUNITS"] = 0
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 1.0
    assert diag is not None
    assert diag.code == "ASSUMED_UNIT"


def test_insunits_unset_falls_back_to_assumed_unit_inch_with_warning(new_doc):
    new_doc.header["$INSUNITS"] = 0
    cfg = Config(input={"assumed_unit": "inch"})
    scale, diag = determine_unit_scale(new_doc, cfg)
    assert scale == 25.4
    assert diag.code == "ASSUMED_UNIT"


def test_unsupported_insunits_warns_and_defaults_to_mm(new_doc):
    new_doc.header["$INSUNITS"] = 2  # feet — unsupported by this tool
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 1.0
    assert diag.code == "UNSUPPORTED_INSUNITS"


def test_convert_line(new_doc):
    msp = new_doc.modelspace()
    line = msp.add_line((0, 0), (1, 2))
    contour = convert_simple_entity(line)
    assert contour.is_closed is False
    assert len(contour.segments) == 1
    assert contour.segments[0].kind == "line"
    assert contour.segments[0].start == (0.0, 0.0)
    assert contour.segments[0].end == (1.0, 2.0)


def test_convert_arc_degrees_to_radians_and_ccw(new_doc):
    msp = new_doc.modelspace()
    arc = msp.add_arc(center=(0, 0), radius=2.0, start_angle=0, end_angle=90)
    contour = convert_simple_entity(arc)
    assert contour.is_closed is False
    seg = contour.segments[0]
    assert seg.kind == "arc"
    assert seg.ccw is True
    assert math.isclose(seg.start[0], 2.0, abs_tol=1e-9)
    assert math.isclose(seg.start[1], 0.0, abs_tol=1e-9)
    assert math.isclose(seg.end[0], 0.0, abs_tol=1e-9)
    assert math.isclose(seg.end[1], 2.0, abs_tol=1e-9)


def test_convert_circle_becomes_two_arc_closed_contour(new_doc):
    msp = new_doc.modelspace()
    circle = msp.add_circle(center=(1, 1), radius=3.0)
    contour = convert_simple_entity(circle)
    assert contour.is_closed is True
    assert len(contour.segments) == 2
    assert all(seg.kind == "arc" for seg in contour.segments)
    assert all(seg.radius == 3.0 for seg in contour.segments)
    assert all(seg.center == (1.0, 1.0) for seg in contour.segments)


def test_convert_lwpolyline_straight_edges(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_lwpolyline([(0, 0), (1, 0), (1, 1)], format="xy")
    contour, diag = convert_polyline_entity(pl)
    assert diag is None
    assert contour.is_closed is False
    assert len(contour.segments) == 2
    assert all(seg.kind == "line" for seg in contour.segments)


def test_convert_lwpolyline_with_bulge_becomes_arc(new_doc):
    msp = new_doc.modelspace()
    # bulge=1.0 on the first vertex -> a semicircle from (0,0) to (2,0)
    pl = msp.add_lwpolyline([(0, 0, 0, 0, 1.0), (2, 0, 0, 0, 0.0)], format="xyseb")
    contour, diag = convert_polyline_entity(pl)
    assert len(contour.segments) == 1
    seg = contour.segments[0]
    assert seg.kind == "arc"
    assert math.isclose(seg.radius, 1.0, abs_tol=1e-9)


def test_convert_closed_lwpolyline(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], format="xy", close=True)
    contour, diag = convert_polyline_entity(pl)
    assert contour.is_closed is True
    assert len(contour.segments) == 4


def test_convert_2d_polyline_entity(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_polyline2d([(0, 0), (1, 0), (1, 1)])
    contour, diag = convert_polyline_entity(pl)
    assert diag is None
    assert len(contour.segments) == 2


def test_convert_3d_polyline_projects_and_warns(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_polyline3d([(0, 0, 5), (1, 0, 5), (1, 1, 5)])
    contour, diag = convert_polyline_entity(pl)
    assert diag is not None
    assert diag.code == "3D_POLYLINE_PROJECTED"
    for seg in contour.segments:
        assert len(seg.start) == 2  # projected to 2D, Z dropped


from dxf_cleaner.reader import read_dxf, ReadResult


def test_read_dxf_mm_file_with_line_and_circle(new_doc, tmp_path):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_circle((5, 5), radius=2.0)
    new_doc.header["$INSUNITS"] = 4
    path = tmp_path / "test.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    assert isinstance(result, ReadResult)
    assert result.unit_scale == 1.0
    assert len(result.contours) == 2
    assert result.diagnostics == []


def test_read_dxf_inch_file_scales_coordinates(new_doc, tmp_path):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    new_doc.header["$INSUNITS"] = 1
    path = tmp_path / "inch.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    line_contour = result.contours[0]
    assert math.isclose(line_contour.segments[0].end[0], 25.4, abs_tol=1e-6)


def test_read_dxf_skips_text_and_records_diagnostic(new_doc, tmp_path):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    msp.add_text("hello")
    path = tmp_path / "with_text.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    assert len(result.contours) == 1
    assert any(d.code == "TEXT_SKIPPED" for d in result.diagnostics)


def test_read_dxf_explodes_blocks(new_doc, tmp_path):
    block = new_doc.blocks.new("B1")
    block.add_line((0, 0), (1, 0))
    msp = new_doc.modelspace()
    msp.add_blockref("B1", (10, 10))
    path = tmp_path / "with_block.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    assert len(result.contours) == 1
    assert result.contours[0].segments[0].start == (10.0, 10.0)


def test_degenerate_lwpolyline_is_skipped_with_diagnostic(tmp_path, new_doc):
    import ezdxf
    from dxf_cleaner.reader import read_dxf
    new_doc.header["$INSUNITS"] = 4
    msp = new_doc.modelspace()
    msp.add_lwpolyline([(3.0, 4.0)], format="xy", close=False)  # single vertex
    msp.add_line((0, 0), (1, 0))  # a valid entity alongside it
    path = tmp_path / "in.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    assert len(result.contours) == 1  # only the LINE survives
    assert "DEGENERATE_ENTITY_SKIPPED" in [d.code for d in result.diagnostics]


def test_single_vertex_polyline_conversion_yields_empty_contour(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_lwpolyline([(1.0, 2.0)], format="xy", close=True)
    contour, diag = convert_polyline_entity(pl)
    assert contour.segments == []


def test_malformed_entity_does_not_abort_the_whole_file(tmp_path, new_doc, monkeypatch):
    from dxf_cleaner import reader as reader_mod
    from dxf_cleaner.reader import read_dxf
    new_doc.header["$INSUNITS"] = 4
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    msp.add_line((2, 0), (3, 0))
    path = tmp_path / "in.dxf"
    new_doc.saveas(path)

    calls = {"n": 0}
    real = reader_mod.convert_simple_entity

    def flaky(entity):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(entity)

    monkeypatch.setattr(reader_mod, "convert_simple_entity", flaky)
    result = read_dxf(str(path), Config())
    assert len(result.contours) == 1
    failed = [d for d in result.diagnostics if d.code == "ENTITY_CONVERSION_FAILED"]
    assert len(failed) == 1
    assert "boom" in failed[0].message


def test_degenerate_spline_is_skipped_with_diagnostic(tmp_path, new_doc, monkeypatch):
    from dxf_cleaner.reader import read_dxf
    new_doc.header["$INSUNITS"] = 4
    msp = new_doc.modelspace()
    msp.add_spline(fit_points=[(0, 0), (1, 1), (2, 0)], degree=2)
    path = tmp_path / "in.dxf"
    new_doc.saveas(path)

    # Simulate a curve that flattens to a single point.
    from dxf_cleaner.stages import flatten as flatten_mod
    monkeypatch.setattr(
        flatten_mod, "_flatten_to_lines", lambda points, layer, handle: None
    )
    monkeypatch.setattr(
        flatten_mod, "_try_fit_circle", lambda points, tol, layer, handle: None
    )

    result = read_dxf(str(path), Config())
    assert result.contours == []
    assert "DEGENERATE_ENTITY_SKIPPED" in [d.code for d in result.diagnostics]


def test_flattened_handles_tracks_spline_and_ellipse_entities(new_doc, tmp_path):
    new_doc.header["$INSUNITS"] = 4
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))  # LINE: not flattened
    ellipse = msp.add_ellipse((0, 0), major_axis=(5, 0), ratio=0.5)  # ELLIPSE: flattened
    path = tmp_path / "flatten.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    ellipse_handle = ellipse.dxf.handle
    assert ellipse_handle in result.flattened_handles
    line_handles = {c.source_handle for c in result.contours} - result.flattened_handles
    assert len(line_handles) == 1
