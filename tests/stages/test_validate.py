import math
from dxf_cleaner.model import Segment, Contour, Part, Diagnostic
from dxf_cleaner.config import ValidateConfig
from dxf_cleaner.stages.validate import validate, ValidationReport


def _square_contour(x0, y0, side, handle="H"):
    p = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    segs = [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


BASE_STATS = dict(
    contour_count_before=1, contour_count_after=1, node_count_before=4, node_count_after=4,
    dedup_count=0, closed_count=0, weld_count=0,
)


def test_clean_run_is_ok():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    report = validate([part], [], ValidateConfig(), BASE_STATS)
    assert report.level == "ok"
    assert report.critical == []
    assert report.info["part_count"] == 1
    assert report.info["weld_count"] == 0


def test_empty_output_is_critical():
    report = validate([], [], ValidateConfig(), BASE_STATS)
    assert report.level == "critical"
    assert any("empty" in c.lower() for c in report.critical)


def test_open_gap_diagnostic_makes_it_critical():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="OPEN_GAP", message="Gap of 0.3mm between (0,0) and (0.3,0)")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "critical"


def test_oversized_part_is_critical():
    huge = Part(exterior=_square_contour(0, 0, 2000, "HUGE"))  # bigger than default 1500x3000 sheet... actually 2000 > 1500 width
    report = validate([huge], [], ValidateConfig(), BASE_STATS)
    assert report.level == "critical"
    assert any("sheet" in c.lower() or "oversized" in c.lower() for c in report.critical)


def test_dropped_entity_diagnostic_is_a_warning_not_critical():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="TEXT_SKIPPED", message="text dropped")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("text" in w.lower() or "dropped" in w.lower() for w in report.warnings)


def test_duplicate_parts_are_a_warning():
    a = Part(exterior=_square_contour(0, 0, 10, "A"))
    b = Part(exterior=_square_contour(0, 0, 10, "B"))  # identical shape and position
    report = validate([a, b], [], ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("duplicate" in w.lower() for w in report.warnings)


def test_hole_smaller_than_material_thickness_is_a_warning():
    outer = _square_contour(0, 0, 100, "OUTER")
    tiny_hole = _square_contour(10, 10, 1.0, "HOLE")  # diameter ~1.13mm
    part = Part(exterior=outer, interiors=[tiny_hole])
    cfg = ValidateConfig(material_thickness=2.0, min_hole_diameter_ratio=1.0)  # min diameter 2.0mm
    report = validate([part], [], cfg, BASE_STATS)
    assert report.level == "warning"
    assert any("hole" in w.lower() for w in report.warnings)
    # The hole is comfortably far (>= 9mm) from its own exterior boundary, well beyond
    # 2x the default kerf width, so no spurious "closer than 2x kerf" warning against
    # itself should be emitted (regression test for exterior/hole self-comparison bug).
    assert not any("close" in w.lower() or "kerf" in w.lower() for w in report.warnings)


def test_hole_far_from_own_exterior_produces_no_spacing_warning():
    outer = _square_contour(0, 0, 100, "OUTER")
    hole = _square_contour(45, 45, 10, "HOLE")  # centered, walls are 45mm thick
    part = Part(exterior=outer, interiors=[hole])
    cfg = ValidateConfig(kerf_width=0.1)  # 2x kerf = 0.2mm, far smaller than the 45mm wall
    report = validate([part], [], cfg, BASE_STATS)
    assert not any("close" in w.lower() or "kerf" in w.lower() for w in report.warnings)


def test_parts_closer_than_2x_kerf_is_a_warning():
    a = Part(exterior=_square_contour(0, 0, 10, "A"))
    b = Part(exterior=_square_contour(10.1, 0, 10, "B"))  # 0.1mm apart, kerf default 0.15 -> 2x=0.3
    report = validate([a, b], [], ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("close" in w.lower() or "kerf" in w.lower() for w in report.warnings)


def test_invalid_polygon_fixed_is_a_warning():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="INVALID_POLYGON_FIXED", message="repaired")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "warning"


def test_contour_opened_by_dedupe_is_critical():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="CONTOUR_OPENED_BY_DEDUPE", message="opened")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "critical"


def test_expected_cleanup_codes_stay_informational_but_visible():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [
        Diagnostic(code="DESPECKLED", message="tiny contour removed"),
        Diagnostic(code="DUPLICATE_SEGMENT_REMOVED", message="dup"),
        Diagnostic(code="OVERLAPPING_SEGMENTS_MERGED", message="merged"),
        Diagnostic(code="COMMON_EDGE", message="shared"),
    ]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "ok"
    assert report.info["diag_despeckled"] == 1
    assert report.info["diag_duplicate_segment_removed"] == 1
    assert report.info["diag_overlapping_segments_merged"] == 1
    assert report.info["diag_common_edge"] == 1


def test_edge_sharing_parts_do_not_trigger_a_spacing_warning():
    a = Part(exterior=_square_contour(0, 0, 100, "A"))
    b = Part(exterior=_square_contour(100, 0, 100, "B"))  # shares the x=100 edge
    report = validate([a, b], [Diagnostic(code="COMMON_EDGE", message="shared")], ValidateConfig(), BASE_STATS)
    assert not any("kerf" in w for w in report.warnings)


def test_parts_slightly_apart_still_trigger_a_spacing_warning():
    a = Part(exterior=_square_contour(0, 0, 100, "A"))
    b = Part(exterior=_square_contour(100.1, 0, 100, "B"))  # 0.1mm gap
    report = validate([a, b], [], ValidateConfig(), BASE_STATS)
    assert any("kerf" in w for w in report.warnings)


def test_every_emitted_diagnostic_code_is_classified_by_validate():
    """A new stage diagnostic code must be explicitly classified here, not
    silently vanish from the report."""
    import pathlib, re
    from dxf_cleaner.stages import validate as validate_module

    root = pathlib.Path(validate_module.__file__).parent.parent
    sources = list((root / "stages").glob("*.py")) + [root / "reader.py"]
    emitted = set()
    for src in sources:
        emitted.update(re.findall(r'code="([A-Z_0-9]+)"', src.read_text()))
    assert emitted, "no diagnostic codes found -- the scan is broken"

    handled = (
        validate_module._CRITICAL_CODES
        | validate_module._WARNING_CODES
        | validate_module._INFO_CODES
        | validate_module._DROPPED_ENTITY_CODES
        | {"ASSUMED_UNIT", "AMBIGUOUS_JUNCTION"}
    )
    assert emitted <= handled, f"unclassified diagnostic codes: {sorted(emitted - handled)}"


def test_edge_sharing_parts_do_not_trigger_an_overlap_warning():
    # Zero-area intersection (a legitimate shared / common-cut edge) is not an
    # overlap, so the near-zero-distance exemption must still hold silently.
    a = Part(exterior=_square_contour(0, 0, 100, "A"))
    b = Part(exterior=_square_contour(100, 0, 100, "B"))  # shares the x=100 edge
    report = validate([a, b], [], ValidateConfig(), BASE_STATS)
    assert not any("overlap" in w for w in report.warnings)


def test_genuinely_overlapping_parts_produce_an_overlap_warning():
    a = Part(exterior=_square_contour(0, 0, 100, "A"))
    b = Part(exterior=_square_contour(50, 0, 100, "B"))  # 50x100mm of real overlap
    report = validate([a, b], [], ValidateConfig(), BASE_STATS)
    overlap_warnings = [w for w in report.warnings if "overlap" in w]
    assert len(overlap_warnings) == 1
    assert "'A'" in overlap_warnings[0] and "'B'" in overlap_warnings[0]
    assert not any("kerf" in w for w in report.warnings)


def test_min_feature_width_below_thickness_is_critical():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    stats = dict(BASE_STATS, min_feature_width_mm=1.0)
    config = ValidateConfig(material_thickness=2.0)

    report = validate([part], [], config, stats)

    assert report.level == "critical"
    assert any("1.0" in c or "1.00" in c for c in report.critical)


def test_min_feature_width_below_double_thickness_is_warning():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    stats = dict(BASE_STATS, min_feature_width_mm=3.0)
    config = ValidateConfig(material_thickness=2.0)

    report = validate([part], [], config, stats)

    assert report.level == "warning"
    assert any("3.0" in w or "3.00" in w for w in report.warnings)


def test_min_feature_width_above_double_thickness_is_ok():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    stats = dict(BASE_STATS, min_feature_width_mm=5.0)
    config = ValidateConfig(material_thickness=2.0)

    report = validate([part], [], config, stats)

    assert report.level == "ok"


def test_raster_low_dpi_diagnostic_is_a_warning():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="RASTER_LOW_DPI", message="Effective resolution 150 DPI is below 300")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("RASTER_LOW_DPI" in w for w in report.warnings)


def test_raster_possible_inverted_diagnostic_is_a_warning():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="RASTER_POSSIBLE_INVERTED", message="Kept-pixel fill ratio 0.90 looks inverted")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("RASTER_POSSIBLE_INVERTED" in w for w in report.warnings)


def test_missing_min_feature_width_is_skipped():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    stats = dict(BASE_STATS)  # no "min_feature_width_mm" key -- DXF input case
    config = ValidateConfig(material_thickness=2.0)

    report = validate([part], [], config, stats)

    assert report.level == "ok"


def test_multiple_parts_from_raster_input_warns_about_micro_joint():
    parts = [Part(exterior=_square_contour(0, 0, 10, "A")), Part(exterior=_square_contour(50, 0, 10, "B"))]
    stats = dict(BASE_STATS, n_parts=2)

    report = validate(parts, [], ValidateConfig(), stats)

    assert report.level == "warning"
    assert any("micro joint" in w.lower() or "n_parts" in w.lower() or "2" in w for w in report.warnings)


def test_single_part_does_not_warn_about_micro_joint():
    part = Part(exterior=_square_contour(0, 0, 10, "A"))
    stats = dict(BASE_STATS, n_parts=1)

    report = validate([part], [], ValidateConfig(), stats)

    assert report.level == "ok"


def test_missing_n_parts_is_skipped():
    part = Part(exterior=_square_contour(0, 0, 10, "A"))
    stats = dict(BASE_STATS)  # no "n_parts" key -- DXF input case

    report = validate([part], [], ValidateConfig(), stats)

    assert report.level == "ok"
