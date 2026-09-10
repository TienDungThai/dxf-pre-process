import pytest
from pydantic import ValidationError
from dxf_cleaner.config import Config, load_config, SnapConfig, DedupeConfig, DespeckleConfig, ValidateConfig


def test_default_config_values():
    cfg = Config()
    assert cfg.input.assumed_unit == "mm"
    assert cfg.flatten.chord_tolerance == 0.02
    assert cfg.flatten.detect_circular_splines is True
    assert cfg.output.dxf_version == "AC1015"
    assert cfg.output.layer_name == "CUT"


def test_load_config_none_path_returns_defaults():
    cfg = load_config(None)
    assert cfg == Config()


def test_load_config_from_yaml_overrides_defaults(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "input:\n  assumed_unit: inch\n"
        "flatten:\n  chord_tolerance: 0.05\n"
    )
    cfg = load_config(str(yaml_path))
    assert cfg.input.assumed_unit == "inch"
    assert cfg.flatten.chord_tolerance == 0.05
    # untouched sections keep defaults
    assert cfg.output.layer_name == "CUT"


def test_config_rejects_invalid_assumed_unit():
    with pytest.raises(Exception):
        Config(input={"assumed_unit": "cm"})


def test_new_phase2_config_defaults():
    cfg = Config()
    assert cfg.snap.tolerance == 0.05
    assert cfg.snap.max_reportable_gap == 2.0
    assert cfg.dedupe.enabled is True
    assert cfg.dedupe.merge_common_edges is False
    assert cfg.despeckle.min_perimeter == 0.5
    assert cfg.despeckle.min_area == 0.1
    assert cfg.validate.sheet_width == 1500
    assert cfg.validate.sheet_height == 3000
    assert cfg.validate.material_thickness == 2.0
    assert cfg.validate.kerf_width == 0.15
    assert cfg.validate.min_hole_diameter_ratio == 1.0


def test_phase2_config_yaml_override(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "snap:\n  tolerance: 0.1\n"
        "dedupe:\n  merge_common_edges: true\n"
    )
    cfg = load_config(str(yaml_path))
    assert cfg.snap.tolerance == 0.1
    assert cfg.dedupe.merge_common_edges is True
    # untouched sections keep defaults
    assert cfg.despeckle.min_area == 0.1


# Task 1: WeldConfig and SimplifyConfig
def test_weld_config_defaults():
    cfg = Config()
    assert cfg.weld.mode == "overlapping"


def test_simplify_config_defaults():
    cfg = Config()
    assert cfg.simplify.enabled is True
    assert cfg.simplify.tolerance == 0.01
    assert cfg.simplify.collinear_angle_deg == 0.1
    assert cfg.simplify.max_area_deviation_pct == 0.1


def test_weld_mode_rejects_invalid_value():
    with pytest.raises(ValidationError):
        Config(weld={"mode": "bogus"})
