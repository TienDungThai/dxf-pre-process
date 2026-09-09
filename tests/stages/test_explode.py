from dxf_cleaner.config import Config
from dxf_cleaner.stages.explode import explode_and_filter


def test_passthrough_entities_are_kept(new_doc):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    msp.add_circle((0, 0), radius=1.0)
    entities, diagnostics = explode_and_filter(msp, Config())
    types = sorted(e.dxftype() for e in entities)
    assert types == ["CIRCLE", "LINE"]
    assert diagnostics == []


def test_text_entities_are_dropped_with_warning(new_doc):
    msp = new_doc.modelspace()
    msp.add_text("hello")
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "TEXT_SKIPPED"


def test_point_and_dimension_are_dropped_silently(new_doc):
    msp = new_doc.modelspace()
    msp.add_point((0, 0))
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert diagnostics == []


def test_3dface_is_dropped_with_warning(new_doc):
    msp = new_doc.modelspace()
    msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert diagnostics[0].code == "3D_ENTITY_SKIPPED"


def test_hatch_is_dropped_silently_by_default(new_doc):
    msp = new_doc.modelspace()
    hatch = msp.add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (1, 0), (1, 1), (0, 1)], is_closed=True)
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert diagnostics == []


def test_single_level_insert_is_exploded_to_its_content(new_doc):
    block = new_doc.blocks.new("BLOCK1")
    block.add_line((0, 0), (1, 0))
    msp = new_doc.modelspace()
    msp.add_blockref("BLOCK1", (10, 10))
    entities, diagnostics = explode_and_filter(msp, Config())
    assert len(entities) == 1
    line = entities[0]
    assert line.dxftype() == "LINE"
    assert tuple(line.dxf.start)[:2] == (10.0, 10.0)
    assert tuple(line.dxf.end)[:2] == (11.0, 10.0)


def test_nested_insert_is_exploded_recursively(new_doc):
    inner = new_doc.blocks.new("INNER")
    inner.add_line((0, 0), (1, 0))
    outer = new_doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (5, 5))
    msp = new_doc.modelspace()
    msp.add_blockref("OUTER", (10, 10))
    entities, diagnostics = explode_and_filter(msp, Config())
    assert len(entities) == 1
    line = entities[0]
    assert line.dxftype() == "LINE"
    assert tuple(line.dxf.start)[:2] == (15.0, 15.0)
    assert tuple(line.dxf.end)[:2] == (16.0, 15.0)


def test_polyface_mesh_is_dropped_with_3d_diagnostic(new_doc):
    msp = new_doc.modelspace()
    mesh = msp.add_polyface()
    mesh.append_face([(0, 0, 0), (1, 0, 0), (1, 1, 1), (0, 1, 1)])
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert [d.code for d in diagnostics] == ["3D_ENTITY_SKIPPED"]


def test_polymesh_is_dropped_with_3d_diagnostic(new_doc):
    msp = new_doc.modelspace()
    msp.add_polymesh(size=(2, 2))
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert [d.code for d in diagnostics] == ["3D_ENTITY_SKIPPED"]


def test_2d_polyline_still_passes_through(new_doc):
    msp = new_doc.modelspace()
    msp.add_polyline2d([(0, 0), (1, 0), (1, 1)])
    entities, diagnostics = explode_and_filter(msp, Config())
    assert [e.dxftype() for e in entities] == ["POLYLINE"]
    assert diagnostics == []


def test_3d_polyline_still_passes_through(new_doc):
    msp = new_doc.modelspace()
    msp.add_polyline3d([(0, 0, 0), (1, 0, 1)])
    entities, diagnostics = explode_and_filter(msp, Config())
    assert [e.dxftype() for e in entities] == ["POLYLINE"]
    assert diagnostics == []
