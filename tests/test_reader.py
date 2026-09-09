from dxf_cleaner.config import Config
from dxf_cleaner.reader import determine_unit_scale


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
