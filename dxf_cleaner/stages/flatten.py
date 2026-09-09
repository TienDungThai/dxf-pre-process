import math
from dxf_cleaner.model import Segment, Contour, Point


def _fit_circle_3pt(p1: Point, p2: Point, p3: Point) -> tuple[Point, float] | None:
    """Circumcircle through 3 points. Returns None if (near-)collinear."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax ** 2 + ay ** 2) * (by - cy) + (bx ** 2 + by ** 2) * (cy - ay) + (cx ** 2 + cy ** 2) * (ay - by)) / d
    uy = ((ax ** 2 + ay ** 2) * (cx - bx) + (bx ** 2 + by ** 2) * (ax - cx) + (cx ** 2 + cy ** 2) * (bx - ax)) / d
    radius = math.dist((ux, uy), p1)
    if radius < 1e-9:
        return None
    return (ux, uy), radius


def _signed_area_sign(points: list[Point], center: Point) -> int:
    """Angular direction of `points` *about `center`*.

    Signed sum of cross products of successive radius vectors (p[i]-center,
    p[i+1]-center). This is the shoelace formula translated so the origin sits
    at the arc's own center; using the raw coordinate origin instead reports the
    winding about (0, 0), which is wrong for any arc not centred there and makes
    the arc get reconstructed as its mirror image across the chord.
    """
    cx, cy = center
    total = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        total += (x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)
    return 1 if total >= 0 else -1


def _try_fit_circle(points: list[Point], tolerance: float, layer: str, handle: str) -> Contour | None:
    if len(points) < 3:
        return None
    p1, p2, p3 = points[0], points[len(points) // 2], points[-1]
    if math.dist(p1, p3) < 1e-9 and len(points) > 3:
        # closed loop where the last sample coincides exactly with the first:
        # fall back to the second-to-last point so the 3-point fit isn't degenerate.
        p3 = points[-2]
    fit = _fit_circle_3pt(p1, p2, p3)
    if fit is None:
        return None
    center, radius = fit
    for x, y in points:
        if abs(math.dist((x, y), center) - radius) > tolerance:
            return None

    is_closed = math.dist(points[0], points[-1]) <= tolerance
    ccw = _signed_area_sign(points, center) > 0

    if is_closed:
        cx, cy = center
        p_start = points[0]
        # split the full circle into two half-circle arcs so it matches the shape
        # produced when reading a native CIRCLE entity (see model.contour_as_full_circle).
        angle_start = math.atan2(p_start[1] - cy, p_start[0] - cx)
        angle_mid = angle_start + (math.pi if ccw else -math.pi)
        p_mid = (cx + radius * math.cos(angle_mid), cy + radius * math.sin(angle_mid))
        seg1 = Segment(kind="arc", start=p_start, end=p_mid, center=center, radius=radius, ccw=ccw)
        seg2 = Segment(kind="arc", start=p_mid, end=p_start, center=center, radius=radius, ccw=ccw)
        return Contour(segments=[seg1, seg2], is_closed=True, source_layer=layer, source_handle=handle)

    seg = Segment(kind="arc", start=points[0], end=points[-1], center=center, radius=radius, ccw=ccw)
    return Contour(segments=[seg], is_closed=False, source_layer=layer, source_handle=handle)


def _flatten_to_lines(points: list[Point], layer: str, handle: str) -> Contour | None:
    """Build a polyline Contour from sampled points.

    Convention: returns None for a degenerate input (fewer than 2 points, or a
    point list that collapses to a single distinct point) rather than producing
    a Contour with zero segments -- callers treat None as "nothing to emit".
    """
    if len(points) < 2:
        return None
    is_closed = math.dist(points[0], points[-1]) <= 1e-6
    if is_closed:
        points = points[:-1]
        if len(points) < 2:
            return None
    segments = [
        Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)])
        for i in range(len(points) - (0 if is_closed else 1))
    ]
    return Contour(segments=segments, is_closed=is_closed, source_layer=layer, source_handle=handle)


def flatten_entity(entity, chord_tolerance: float, detect_circular: bool) -> Contour | None:
    layer = entity.dxf.layer
    handle = entity.dxf.handle
    raw_points = list(entity.flattening(chord_tolerance))
    points: list[Point] = [(p.x, p.y) for p in raw_points]

    if detect_circular:
        circle_contour = _try_fit_circle(points, chord_tolerance, layer, handle)
        if circle_contour is not None:
            return circle_contour

    return _flatten_to_lines(points, layer, handle)
