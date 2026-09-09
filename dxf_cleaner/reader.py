import math

from dxf_cleaner.config import Config
from dxf_cleaner.model import Diagnostic, Segment, Contour, Point

_SUPPORTED_UNIT_SCALES = {1: 25.4, 4: 1.0}
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
    return 1.0, Diagnostic(
        code="UNSUPPORTED_INSUNITS",
        message=f"$INSUNITS={insunits} is not supported (only mm/inch); treating as mm",
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
