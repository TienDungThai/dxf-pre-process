import math
from shapely import STRtree
from shapely.geometry import Point as ShapelyPoint

from dxf_cleaner.model import Segment, Contour, Diagnostic, Point, reverse_segment


def _find(parent: list[int], i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[ra] = rb


def snap_and_chain(
    contours: list[Contour], tolerance: float, max_reportable_gap: float
) -> tuple[list[Contour], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    segments: list[Segment] = []
    layers: list[str] = []
    handles: list[str] = []
    for contour in contours:
        for seg in contour.segments:
            segments.append(seg)
            layers.append(contour.source_layer)
            handles.append(contour.source_handle)

    n = len(segments)
    if n == 0:
        return [], diagnostics

    endpoint_coords: list[Point] = []
    for seg in segments:
        endpoint_coords.append(seg.start)
        endpoint_coords.append(seg.end)

    points = [ShapelyPoint(p) for p in endpoint_coords]
    tree = STRtree(points)
    parent = list(range(len(endpoint_coords)))
    for i, p in enumerate(points):
        for j in tree.query(p, predicate="dwithin", distance=tolerance):
            j = int(j)
            if j != i:
                _union(parent, i, j)

    cluster_points: dict[int, list[Point]] = {}
    for i, coord in enumerate(endpoint_coords):
        cluster_points.setdefault(_find(parent, i), []).append(coord)

    canonical: dict[int, Point] = {
        root: (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))
        for root, coords in cluster_points.items()
    }

    def canon_point(endpoint_idx: int) -> Point:
        return canonical[_find(parent, endpoint_idx)]

    snapped_with_indices = [
        (i, Segment(
            kind=seg.kind,
            start=canon_point(2 * i),
            end=canon_point(2 * i + 1),
            center=seg.center,
            radius=seg.radius,
            ccw=seg.ccw,
        ))
        for i, seg in enumerate(segments)
    ]
    # Filter out zero-length segments
    snapped_with_indices = [
        (orig_idx, seg) for orig_idx, seg in snapped_with_indices
        if seg.start != seg.end
    ]
    snapped_segments = [seg for _, seg in snapped_with_indices]

    adjacency: dict[Point, list[tuple[int, str]]] = {}
    for i, seg in enumerate(snapped_segments):
        adjacency.setdefault(seg.start, []).append((i, "start"))
        adjacency.setdefault(seg.end, []).append((i, "end"))

    used = [False] * len(snapped_segments)
    chains: list[tuple[list[Segment], bool, str, str]] = []
    # Map filtered segment index back to original segment index
    filtered_to_orig = [orig_idx for orig_idx, _ in snapped_with_indices]

    for i in range(len(snapped_segments)):
        if used[i]:
            continue
        used[i] = True
        chain = [snapped_segments[i]]
        chain_start = snapped_segments[i].start
        current_end = snapped_segments[i].end
        closed = False
        while True:
            if current_end == chain_start:
                closed = True
                break
            candidates = [(idx, which) for idx, which in adjacency.get(current_end, []) if not used[idx]]
            if not candidates:
                break
            if len(candidates) > 1:
                diagnostics.append(Diagnostic(
                    code="AMBIGUOUS_JUNCTION",
                    message=f"Multiple unvisited segments meet at {current_end}; chaining picked one arbitrarily",
                ))
            idx, which = candidates[0]
            used[idx] = True
            seg = snapped_segments[idx]
            if which == "end":
                seg = reverse_segment(seg)
            chain.append(seg)
            current_end = seg.end
        orig_idx = filtered_to_orig[i]
        chains.append((chain, closed, layers[orig_idx], handles[orig_idx]))

    result_contours = [
        Contour(segments=segs, is_closed=closed, source_layer=layer, source_handle=handle)
        for segs, closed, layer, handle in chains
    ]

    open_ends: list[tuple[Point, int]] = []
    for ci, (segs, closed, _, _) in enumerate(chains):
        if not closed:
            open_ends.append((segs[0].start, ci))
            open_ends.append((segs[-1].end, ci))

    if open_ends:
        end_points = [ShapelyPoint(p) for p, _ in open_ends]
        end_tree = STRtree(end_points)
        # For each pair of distinct open chains, find the single closest gap
        # between any of their free ends, and report it once (if within range).
        best_by_chain_pair: dict[tuple[int, int], tuple[float, Point, Point]] = {}
        for i, (p, chain_i) in enumerate(open_ends):
            for j in end_tree.query(end_points[i], predicate="dwithin", distance=max_reportable_gap):
                j = int(j)
                if j == i:
                    continue
                other_point, other_chain = open_ends[j]
                if other_chain == chain_i:
                    continue
                dist = math.dist(p, other_point)
                if dist <= tolerance:
                    continue
                key = (min(chain_i, other_chain), max(chain_i, other_chain))
                existing = best_by_chain_pair.get(key)
                if existing is None or dist < existing[0]:
                    best_by_chain_pair[key] = (dist, p, other_point)

        for (dist, p, other_point) in best_by_chain_pair.values():
            diagnostics.append(Diagnostic(
                code="OPEN_GAP",
                message=f"Gap of {dist:.4f}mm between {p} and {other_point}",
            ))

    return result_contours, diagnostics
