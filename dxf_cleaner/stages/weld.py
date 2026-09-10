import math
from typing import Literal

from shapely import make_valid
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from dxf_cleaner.model import Segment, Contour, Diagnostic, Point, fit_circle_3pt, signed_area_sign

_MIN_OVERLAP_AREA = 1e-6
_MIN_ARC_WINDOW = 5  # fewest consecutive boundary points that may form one arc


def _to_valid_polygon(contour: Contour, arc_tolerance: float, diagnostics: list[Diagnostic]) -> Polygon | None:
    poly = Polygon(contour.to_shapely(arc_tolerance))
    if poly.is_valid:
        return poly
    fixed = make_valid(poly)
    diagnostics.append(Diagnostic(
        code="INVALID_POLYGON_FIXED",
        message="Repaired self-intersecting polygon before weld",
        handle=contour.source_handle,
    ))
    if fixed.geom_type != "Polygon":
        candidates = [g for g in getattr(fixed, "geoms", []) if g.geom_type == "Polygon"]
        if not candidates:
            diagnostics.append(Diagnostic(
                code="POLYGON_UNRECOVERABLE",
                message="Could not recover a usable polygon for weld",
                handle=contour.source_handle,
            ))
            return None
        fixed = max(candidates, key=lambda g: g.area)
    return fixed


def _cluster_by_overlap(polygons: list[Polygon]) -> list[list[int]]:
    """Union-find over pairs whose intersection has positive area."""
    n = len(polygons)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    tree = STRtree(polygons)
    for i, poly in enumerate(polygons):
        for j in tree.query(poly, predicate="intersects"):
            j = int(j)
            if j <= i:
                continue
            other = polygons[j]
            # `overlaps()` is positive-area interior intersection with NEITHER
            # polygon containing the other -- exactly "genuinely overlapping"
            # for weld purposes. Plain intersection-area would also fire for a
            # hole/island fully nested inside another closed contour (e.g. a
            # part's hole and the island sitting inside it), which is a
            # legitimate containment relationship for the hierarchy stage to
            # sort out, not something weld should merge away.
            if poly.overlaps(other) and poly.intersection(other).area > _MIN_OVERLAP_AREA:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return list(clusters.values())


def _ring_points(ring) -> list[Point]:
    coords = list(ring.coords)
    if math.dist(coords[0], coords[-1]) < 1e-9:
        coords = coords[:-1]
    return [(x, y) for x, y in coords]


def _detect_arcs_in_ring(points: list[Point], tolerance: float) -> list[Segment]:
    """Segment-wise scan: greedily grow an arc window from each start point as far
    as it still fits a single circle within `tolerance`; fall back to a line
    segment for any stretch too short or too irregular to be an arc.

    `i` is an absolute (unmodded) cursor tracking total ring edges consumed so
    far, starting at 0. The window's end `j` is bounded by `j < n` (n = total
    ring points) so that the cumulative edges consumed across all iterations
    never exceeds `n` — i.e. the scan can wrap at most once around the ring's
    closing seam and always terminates having covered every edge exactly once.
    (An earlier draft bounded `j` by `i + n` instead of `n`, which let a window
    starting near the end of the ring re-wrap past the seam a second time,
    producing a non-contiguous result — see task-2 report for the trace.)
    """
    n = len(points)
    if n < 3:
        return [Segment(kind="line", start=points[i], end=points[(i + 1) % n]) for i in range(n)]

    segments: list[Segment] = []
    i = 0
    while i < n:
        best_end = None
        best_fit = None
        j = i + _MIN_ARC_WINDOW - 1
        while j < n:  # cap total consumption at n edges; allows wrap past the seam once
            window = [points[k % n] for k in range(i, j + 1)]
            fit = fit_circle_3pt(window[0], window[len(window) // 2], window[-1])
            if fit is None:
                break
            center, radius = fit
            if all(abs(math.dist(p, center) - radius) <= tolerance for p in window):
                best_end = j
                best_fit = (center, radius)
                j += 1
            else:
                break
        if best_end is not None:
            center, radius = best_fit
            window = [points[k % n] for k in range(i, best_end + 1)]
            ccw = signed_area_sign(window, center) > 0
            segments.append(Segment(
                kind="arc", start=points[i % n], end=points[best_end % n],
                center=center, radius=radius, ccw=ccw,
            ))
            i = best_end
        else:
            segments.append(Segment(kind="line", start=points[i % n], end=points[(i + 1) % n]))
            i += 1
        if i >= n:
            break
    return segments


def _polygon_to_contours(poly: Polygon, arc_tolerance: float, handle: str, layer: str) -> list[Contour]:
    out: list[Contour] = []
    for ring, is_exterior in [(poly.exterior, True)] + [(r, False) for r in poly.interiors]:
        points = _ring_points(ring)
        segments = _detect_arcs_in_ring(points, arc_tolerance)
        ring_handle = handle if is_exterior else f"{handle}_hole{len(out)}"
        out.append(Contour(segments=segments, is_closed=True, source_layer=layer, source_handle=ring_handle))
    return out


def _warn_contained_polygons(
    cluster: list[int], polygons: list[Polygon], valid_closed: list[Contour], diagnostics: list[Diagnostic]
) -> None:
    """In "all" mode every closed contour in the cluster is unioned together,
    including a hole/island fully contained inside another contour's
    exterior -- `unary_union` of a containing polygon and a contained one
    just returns the containing polygon, silently deleting the contained
    one. Warn per contained contour so the data loss is auditable."""
    lost: set[int] = set()
    for a in cluster:
        for b in cluster:
            if a == b or a in lost:
                continue
            if polygons[a].within(polygons[b]) and not polygons[a].equals(polygons[b]):
                lost.add(a)
    for i in sorted(lost):
        diagnostics.append(Diagnostic(
            code="HOLES_LOST_IN_WELD_ALL",
            message="Contour fully contained by another in the same weld-all cluster; "
                    "will be absorbed and lost in the union",
            handle=valid_closed[i].source_handle,
        ))


def weld_contours(
    contours: list[Contour], mode: Literal["off", "overlapping", "all"], arc_tolerance: float = 0.02
) -> tuple[list[Contour], list[Diagnostic], set[str]]:
    if mode == "off":
        return contours, [], set()

    diagnostics: list[Diagnostic] = []
    open_contours = [c for c in contours if not c.is_closed]
    closed_contours = [c for c in contours if c.is_closed]

    polygons: list[Polygon] = []
    valid_closed: list[Contour] = []
    unrecoverable: list[Contour] = []
    for c in closed_contours:
        poly = _to_valid_polygon(c, arc_tolerance, diagnostics)
        if poly is None:
            unrecoverable.append(c)
            continue
        polygons.append(poly)
        valid_closed.append(c)

    if not polygons:
        return list(open_contours) + unrecoverable, diagnostics, set()

    if mode == "all":
        clusters = [list(range(len(polygons)))]
    else:
        clusters = _cluster_by_overlap(polygons)

    result: list[Contour] = list(open_contours) + unrecoverable
    welded_handles: set[str] = set()
    for cluster in clusters:
        if len(cluster) < 2:
            result.append(valid_closed[cluster[0]])
            continue
        cluster_polys = [polygons[i] for i in cluster]
        if mode == "all":
            _warn_contained_polygons(cluster, polygons, valid_closed, diagnostics)
        union_geom = unary_union(cluster_polys)
        pieces = list(union_geom.geoms) if isinstance(union_geom, MultiPolygon) else [union_geom]
        base_handle = "_".join(sorted(valid_closed[i].source_handle for i in cluster))
        layer = valid_closed[cluster[0]].source_layer
        for idx, piece in enumerate(pieces):
            piece_handle = f"WELD_{base_handle}" if len(pieces) == 1 else f"WELD_{base_handle}_{idx}"
            new_contours = _polygon_to_contours(piece, arc_tolerance, piece_handle, layer)
            for nc in new_contours:
                welded_handles.add(nc.source_handle)
            result.extend(new_contours)
        diagnostics.append(Diagnostic(
            code="CONTOURS_WELDED",
            message=f"Merged {len(cluster)} overlapping contours into {len(pieces)} welded contour(s)",
            handle=base_handle,
        ))

    return result, diagnostics, welded_handles
