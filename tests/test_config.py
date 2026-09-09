import pytest
from dxf_cleaner.config import Config, load_config


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
