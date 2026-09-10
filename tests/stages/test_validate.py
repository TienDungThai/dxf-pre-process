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
