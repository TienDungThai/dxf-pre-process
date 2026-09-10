import math

from dxf_cleaner.model import Segment, Contour, Diagnostic, Point, contour_signed_area


def _angle_of(seg: Segment) -> float:
    return math.atan2(seg.end[1] - seg.start[1], seg.end[0] - seg.start[0])


def _merge_collinear_lines(segments: list[Segment], collinear_angle_deg: float, is_closed: bool) -> list[Segment]:
    """Merge consecutive `line` segments whose direction differs by less than
    `collinear_angle_deg`. Arc segments are never merged and act as boundaries."""
    if not segments:
        return segments
    tol_rad = math.radians(collinear_angle_deg)
    merged: list[Segment] = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if prev.kind == "line" and seg.kind == "line" and math.dist(prev.end, seg.start) < 1e-9:
            angle_diff = abs((_angle_of(prev) - _angle_of(seg) + math.pi) % (2 * math.pi) - math.pi)
            if angle_diff < tol_rad:
                merged[-1] = Segment(kind="line", start=prev.start, end=seg.end)
                continue
        merged.append(seg)
    # wraparound merge for closed contours: first and last segment may now be collinear too
    if is_closed and len(merged) > 1:
        first, last = merged[0], merged[-1]
        if first.kind == "line" and last.kind == "line" and math.dist(last.end, first.start) < 1e-9:
            angle_diff = abs((_angle_of(last) - _angle_of(first) + math.pi) % (2 * math.pi) - math.pi)
            if angle_diff < tol_rad:
                merged[0] = Segment(kind="line", start=last.start, end=first.end)
                merged.pop()
    return merged


def _points_to_line_segments(points: list[Point], is_closed: bool) -> list[Segment]:
    count = len(points) if is_closed else len(points) - 1
    return [Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)]) for i in range(count)]


def _simplify_one(contour: Contour, tolerance: float, collinear_angle_deg: float,
                   max_area_deviation_pct: float, arc_tolerance: float) -> tuple[Contour, Diagnostic | None]:
    has_arcs = any(s.kind == "arc" for s in contour.segments)
    before_area = abs(contour_signed_area(contour, arc_tolerance))

    if has_arcs:
        # Arc segments already carry minimal, exact geometry -- only the
        # collinear-line-merge pass applies; Douglas-Peucker would discretize
        # and destroy them.
        new_segments = _merge_collinear_lines(contour.segments, collinear_angle_deg, contour.is_closed)
        candidate = Contour(segments=new_segments, is_closed=contour.is_closed,
                             source_layer=contour.source_layer, source_handle=contour.source_handle)
    else:
        shape = contour.to_shapely(arc_tolerance)
        simplified = shape.simplify(tolerance, preserve_topology=True)
        points = [(x, y) for x, y in simplified.coords]
        if contour.is_closed and len(points) > 1 and math.dist(points[0], points[-1]) < 1e-9:
            points = points[:-1]
        new_segments = _points_to_line_segments(points, contour.is_closed)
        new_segments = _merge_collinear_lines(new_segments, collinear_angle_deg, contour.is_closed)
        candidate = Contour(segments=new_segments, is_closed=contour.is_closed,
                             source_layer=contour.source_layer, source_handle=contour.source_handle)

    if len(candidate.segments) < 1:
        return contour, None

    after_area = abs(contour_signed_area(candidate, arc_tolerance))
    if before_area > 1e-9:
        deviation_pct = abs(after_area - before_area) / before_area * 100
        if deviation_pct > max_area_deviation_pct:
            return contour, Diagnostic(
                code="SIMPLIFY_REVERTED_AREA_DEVIATION",
                message=f"Simplify reverted: area deviated {deviation_pct:.3f}% "
                        f"(limit {max_area_deviation_pct}%)",
                handle=contour.source_handle,
            )
    return candidate, None


def simplify_contours(
    contours: list[Contour], touched_handles: set[str], tolerance: float,
    collinear_angle_deg: float, max_area_deviation_pct: float, arc_tolerance: float = 0.02,
) -> tuple[list[Contour], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    result: list[Contour] = []
    for contour in contours:
        if contour.source_handle not in touched_handles:
            result.append(contour)
            continue
        simplified, revert_diag = _simplify_one(
            contour, tolerance, collinear_angle_deg, max_area_deviation_pct, arc_tolerance
        )
        result.append(simplified)
        if revert_diag is not None:
            diagnostics.append(revert_diag)
    return result, diagnostics
