from dxf_cleaner.config import Config
from dxf_cleaner.model import Diagnostic

_KEEP_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"}
_WARN_TYPES = {
    "TEXT": "TEXT_SKIPPED",
    "MTEXT": "TEXT_SKIPPED",
    "3DFACE": "3D_ENTITY_SKIPPED",
    "SOLID": "3D_ENTITY_SKIPPED",
    "MESH": "3D_ENTITY_SKIPPED",
}
# Entity types we know we drop silently (as opposed to types we've simply
# never seen). Kept distinct from the implicit catch-all in _classify so a
# future change can log/count "known silent drops" separately from truly
# unrecognized entity types without changing behavior here.
_SILENT_DROP_TYPES = {"DIMENSION", "LEADER", "POINT", "HATCH"}
# POLYLINE sub-modes that represent an actual 2D/3D path (as opposed to a mesh).
_POLYLINE_2D_3D_MODES = {"AcDb2dPolyline", "AcDb3dPolyline"}


def _explode_insert(insert, diagnostics: list[Diagnostic]) -> list:
    result = []
    for entity in insert.virtual_entities():
        if entity.dxftype() == "INSERT":
            result.extend(_explode_insert(entity, diagnostics))
        else:
            result.extend(_classify(entity, diagnostics))
    return result


def _classify(entity, diagnostics: list[Diagnostic]) -> list:
    dxftype = entity.dxftype()
    if dxftype == "POLYLINE":
        # A POLYLINE can also be a polyface/polygon mesh (3D mesh faces, not a
        # 2D cutting path). Chaining unrelated mesh face vertices into one
        # contour produces garbage, so drop those with a diagnostic.
        mode = entity.get_mode() if hasattr(entity, "get_mode") else "AcDb2dPolyline"
        if mode not in _POLYLINE_2D_3D_MODES:
            handle = getattr(entity.dxf, "handle", None)
            diagnostics.append(Diagnostic(
                code="3D_ENTITY_SKIPPED",
                message=f"POLYLINE mesh entity ({mode}) skipped",
                handle=handle,
            ))
            return []
        return [entity]
    if dxftype in _KEEP_TYPES:
        return [entity]
    if dxftype == "INSERT":
        return _explode_insert(entity, diagnostics)
    if dxftype in _WARN_TYPES:
        handle = getattr(entity.dxf, "handle", None)
        diagnostics.append(Diagnostic(code=_WARN_TYPES[dxftype], message=f"{dxftype} entity skipped", handle=handle))
        return []
    if dxftype in _SILENT_DROP_TYPES:
        return []
    # Any other unrecognized entity type is also dropped silently.
    return []


def explode_and_filter(entities, config: Config) -> tuple[list, list[Diagnostic]]:
    """Recursively explode INSERTs and drop unsupported entities.

    Returns (kept_entities, diagnostics) where kept_entities only contains
    LINE/ARC/CIRCLE/LWPOLYLINE/POLYLINE/SPLINE/ELLIPSE entities.
    """
    diagnostics: list[Diagnostic] = []
    kept: list = []
    for entity in entities:
        kept.extend(_classify(entity, diagnostics))
    return kept, diagnostics
