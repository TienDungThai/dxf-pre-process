import math

from ezdxf.math import bulge_to_arc

from dxf_cleaner.config import Config
from dxf_cleaner.model import Diagnostic, Segment, Contour, Point

# AutoCAD $INSUNITS code -> factor to convert that unit into mm. Covers every
# unit a real-world mechanical DXF is plausibly authored in; exotic codes
# (angstroms, light years, ...) fall through to the unsupported-critical path
# below rather than guessing.
_SUPPORTED_UNIT_SCALES = {
    1: 25.4,       # inches
    2: 304.8,      # feet
    4: 1.0,        # millimeters
    5: 10.0,       # centimeters
    6: 1000.0,     # meters
    10: 914.4,     # yards
    13: 0.001,     # microns
    14: 100.0,     # decimeters
}
_ASSUMED_UNIT_SCALES = {"mm": 1.0, "inch": 25.4}


def determine_unit_scale(doc, config: Config) -> tuple[float, Diagnostic | None]:
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits in _SUPPORTED_UNIT_SCALES:
        return _SUPPORTED_UNIT_SCALES[insunits], None
    if insunits == 0:
        scale = _ASSUMED_UNIT_SCALES[config.input.assumed_unit]
        return scale, Diagnostic(
            code="ASSUMED_UNIT",
            message=f"$INSUNITS not set in file; assuming {config.input.assumed_unit} per config",
        )
    # Refuse to guess: silently treating an unrecognized unit as mm can
    # produce a wrong-by-orders-of-magnitude cut file (see UNSUPPORTED_INSUNITS
    # in validate.py, which escalates this to critical -- output is NOT written
    # unless --force is used).
    return 1.0, Diagnostic(
        code="UNSUPPORTED_INSUNITS",
        message=f"$INSUNITS={insunits} is not a recognized length unit; refusing to guess a scale "
                f"(treated as 1:1, almost certainly wrong -- re-export the DXF in mm or inches)",
    )


def _p2(v) -> Point:
    """Convert ezdxf vertex to (x, y) tuple."""
    return (v.x, v.y)


def convert_simple_entity(entity) -> Contour:
    """Convert a simple DXF entity (LINE/ARC/CIRCLE) to a Contour.

    Args:
        entity: An ezdxf entity with dxftype() in {"LINE", "ARC", "CIRCLE"}

    Returns:
        A Contour with the appropriate segments and metadata.

    Raises:
        ValueError: If the entity type is not supported.
    """
    dxftype = entity.dxftype()
    layer = entity.dxf.layer
    handle = entity.dxf.handle

    if dxftype == "LINE":
        seg = Segment(kind="line", start=_p2(entity.dxf.start), end=_p2(entity.dxf.end))
        return Contour(segments=[seg], is_closed=False, source_layer=layer, source_handle=handle)

    if dxftype == "ARC":
        center = _p2(entity.dxf.center)
        radius = entity.dxf.radius
        # Convert angles from degrees to radians
        a0 = math.radians(entity.dxf.start_angle)
        a1 = math.radians(entity.dxf.end_angle)
        # Calculate start and end points using polar coordinates
        start = (center[0] + radius * math.cos(a0), center[1] + radius * math.sin(a0))
        end = (center[0] + radius * math.cos(a1), center[1] + radius * math.sin(a1))
        # ARC entities are always defined CCW from start_angle to end_angle
        seg = Segment(kind="arc", start=start, end=end, center=center, radius=radius, ccw=True)
        return Contour(segments=[seg], is_closed=False, source_layer=layer, source_handle=handle)

    if dxftype == "CIRCLE":
        center = _p2(entity.dxf.center)
        radius = entity.dxf.radius
        # Split circle into two semicircular arcs to form a closed contour
        p_east = (center[0] + radius, center[1])
        p_west = (center[0] - radius, center[1])
        seg1 = Segment(kind="arc", start=p_east, end=p_west, center=center, radius=radius, ccw=True)
        seg2 = Segment(kind="arc", start=p_west, end=p_east, center=center, radius=radius, ccw=True)
        return Contour(segments=[seg1, seg2], is_closed=True, source_layer=layer, source_handle=handle)

    raise ValueError(f"convert_simple_entity does not handle {dxftype}")


def _segment_from_bulge(p0: Point, p1: Point, bulge: float) -> Segment:
    if bulge == 0:
        return Segment(kind="line", start=p0, end=p1)
    center, _start_angle, _end_angle, radius = bulge_to_arc(p0, p1, bulge)
    return Segment(kind="arc", start=p0, end=p1, center=(center.x, center.y), radius=radius, ccw=bulge > 0)


def convert_polyline_entity(entity) -> tuple[Contour, Diagnostic | None]:
    """Convert a LWPOLYLINE or 2D/3D POLYLINE entity to a Contour, decoding bulges to arcs.

    Args:
        entity: An ezdxf entity with dxftype() in {"LWPOLYLINE", "POLYLINE"}

    Returns:
        A tuple of (Contour, Diagnostic | None). The diagnostic is set when a
        3D POLYLINE was projected to Z=0.

    Raises:
        ValueError: If the entity type is not supported.
    """
    dxftype = entity.dxftype()
    layer = entity.dxf.layer
    handle = entity.dxf.handle
    diagnostic: Diagnostic | None = None

    if dxftype == "LWPOLYLINE":
        raw_points = entity.get_points("xyb")
        vertices = [((x, y), b) for x, y, b in raw_points]
        is_closed = entity.closed
    elif dxftype == "POLYLINE":
        is_3d = any(abs(v.dxf.location.z) > 1e-9 for v in entity.vertices)
        vertices = [((v.dxf.location.x, v.dxf.location.y), v.dxf.bulge) for v in entity.vertices]
        is_closed = entity.is_closed
        if is_3d:
            diagnostic = Diagnostic(
                code="3D_POLYLINE_PROJECTED",
                message="3D POLYLINE projected to Z=0",
                handle=handle,
            )
    else:
        raise ValueError(f"convert_polyline_entity does not handle {dxftype}")

    n = len(vertices)
    if n < 2:
        # Degenerate polyline (0 or 1 vertices): emit an empty contour so the
        # caller (read_dxf) drops it with a diagnostic instead of building a
        # zero-length segment.
        empty = Contour(segments=[], is_closed=is_closed, source_layer=layer, source_handle=handle)
        return empty, diagnostic
    count = n if is_closed else n - 1
    segments = [
        _segment_from_bulge(vertices[i][0], vertices[(i + 1) % n][0], vertices[i][1])
        for i in range(count)
    ]
    contour = Contour(segments=segments, is_closed=is_closed, source_layer=layer, source_handle=handle)
    return contour, diagnostic


from dataclasses import dataclass
import ezdxf
from dxf_cleaner.model import scale_contour
from dxf_cleaner.stages.explode import explode_and_filter
from dxf_cleaner.stages.flatten import flatten_entity


@dataclass
class ReadResult:
    contours: list[Contour]
    diagnostics: list[Diagnostic]
    unit_scale: float
    flattened_handles: set[str]
    raster_stats: dict[str, float] | None = None
    raster_preview_data: dict | None = None


def _convert_entity(entity, config: Config) -> tuple[Contour | None, Diagnostic | None]:
    dxftype = entity.dxftype()
    if dxftype in {"LINE", "ARC", "CIRCLE"}:
        return convert_simple_entity(entity), None
    if dxftype in {"LWPOLYLINE", "POLYLINE"}:
        return convert_polyline_entity(entity)
    if dxftype in {"SPLINE", "ELLIPSE"}:
        contour = flatten_entity(
            entity,
            chord_tolerance=config.flatten.chord_tolerance,
            detect_circular=config.flatten.detect_circular_splines,
        )
        if contour is None:
            # Degenerate curve (fewer than 2 distinct sample points): return an
            # empty contour so read_dxf reports DEGENERATE_ENTITY_SKIPPED.
            contour = Contour(segments=[], is_closed=False,
                              source_layer=entity.dxf.layer, source_handle=entity.dxf.handle)
        return contour, None
    return None, None


def read_dxf(path: str, config: Config) -> ReadResult:
    doc = ezdxf.readfile(path)
    unit_scale, unit_diag = determine_unit_scale(doc, config)

    diagnostics: list[Diagnostic] = []
    if unit_diag is not None:
        diagnostics.append(unit_diag)

    kept_entities, explode_diags = explode_and_filter(doc.modelspace(), config)
    diagnostics.extend(explode_diags)

    contours: list[Contour] = []
    flattened_handles: set[str] = set()
    for entity in kept_entities:
        handle = getattr(entity.dxf, "handle", None)
        try:
            contour, diag = _convert_entity(entity, config)
        except Exception as exc:  # one malformed entity must not abort the file
            diagnostics.append(Diagnostic(
                code="ENTITY_CONVERSION_FAILED",
                message=f"{entity.dxftype()} entity could not be converted: {exc}",
                handle=handle,
            ))
            continue
        if diag is not None:
            diagnostics.append(diag)
        if contour is None:
            continue
        if not contour.segments:
            diagnostics.append(Diagnostic(
                code="DEGENERATE_ENTITY_SKIPPED",
                message=f"{entity.dxftype()} entity produced no geometry; skipped",
                handle=handle,
            ))
            continue
        if entity.dxftype() in {"SPLINE", "ELLIPSE"}:
            flattened_handles.add(contour.source_handle)
        contours.append(scale_contour(contour, unit_scale))

    return ReadResult(contours=contours, diagnostics=diagnostics, unit_scale=unit_scale,
                       flattened_handles=flattened_handles)
