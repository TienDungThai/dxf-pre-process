import math
from dxf_cleaner.model import Segment, Contour, Point


def _is_closed_loop(points: list[tuple[float, float, float]], tol: float = 1e-6) -> bool:
    return math.dist(points[0][:2], points[-1][:2]) <= tol


def _flatten_to_lines(points: list[Point], layer: str, handle: str) -> Contour:
    is_closed = math.dist(points[0], points[-1]) <= 1e-6
    if is_closed:
        points = points[:-1]  # avoid a zero-length closing segment; is_closed implies wraparound
    segments = [
        Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)])
        for i in range(len(points) - (0 if is_closed else 1))
    ]
    return Contour(segments=segments, is_closed=is_closed, source_layer=layer, source_handle=handle)


def flatten_entity(entity, chord_tolerance: float, detect_circular: bool) -> Contour:
    """Flatten a SPLINE or ELLIPSE entity into a Contour of line segments within
    `chord_tolerance` of the true curve. `detect_circular` is accepted here as part
    of the public signature that Task 8 will extend with real circular-arc
    re-detection; for now it does not change behavior."""
    layer = entity.dxf.layer
    handle = entity.dxf.handle
    raw_points = list(entity.flattening(chord_tolerance))
    points: list[Point] = [(p.x, p.y) for p in raw_points]

    return _flatten_to_lines(points, layer, handle)
