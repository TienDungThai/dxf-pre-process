from shapely import STRtree, make_valid
from shapely.geometry import Polygon

from dxf_cleaner.model import Contour, Part, Diagnostic, contour_signed_area, reverse_segment


def _oriented_contour(contour: Contour, ccw: bool) -> Contour:
    is_ccw = contour_signed_area(contour) > 0
    if is_ccw == ccw:
        return contour
    return Contour(
        segments=[reverse_segment(seg) for seg in reversed(contour.segments)],
        is_closed=contour.is_closed,
        source_layer=contour.source_layer,
        source_handle=contour.source_handle,
    )


def build_hierarchy(contours: list[Contour], arc_tolerance: float = 0.02) -> tuple[list[Part], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    closed: list[Contour] = []
    for c in contours:
        if c.is_closed:
            closed.append(c)
        else:
            diagnostics.append(Diagnostic(
                code="OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY",
                message="Contour is not closed; excluded from exterior/hole hierarchy",
                handle=c.source_handle,
            ))

    polygons: list[Polygon] = []
    valid_closed: list[Contour] = []
    for c in closed:
        poly = Polygon(c.to_shapely(arc_tolerance))
        if not poly.is_valid:
            fixed = make_valid(poly)
            diagnostics.append(Diagnostic(code="INVALID_POLYGON_FIXED", message="Repaired self-intersecting polygon", handle=c.source_handle))
            if fixed.geom_type != "Polygon":
                candidates = [g for g in getattr(fixed, "geoms", []) if g.geom_type == "Polygon"]
                if not candidates:
                    diagnostics.append(Diagnostic(code="POLYGON_UNRECOVERABLE", message="Could not recover a usable polygon", handle=c.source_handle))
                    continue
                fixed = max(candidates, key=lambda g: g.area)
            poly = fixed
        polygons.append(poly)
        valid_closed.append(c)

    n = len(polygons)
    if n == 0:
        return [], diagnostics

    tree = STRtree(polygons)
    depth = [0] * n
    parent_of: list[int | None] = [None] * n
    for i, poly in enumerate(polygons):
        containers = [int(j) for j in tree.query(poly, predicate="within") if int(j) != i]
        depth[i] = len(containers)
        if containers:
            parent_of[i] = min(containers, key=lambda j: polygons[j].area)

    parts: dict[int, Part] = {
        i: Part(exterior=_oriented_contour(valid_closed[i], ccw=True))
        for i in range(n) if depth[i] % 2 == 0
    }
    for i in range(n):
        if depth[i] % 2 == 1 and parent_of[i] is not None and parent_of[i] in parts:
            parts[parent_of[i]].interiors.append(_oriented_contour(valid_closed[i], ccw=False))

    return list(parts.values()), diagnostics
