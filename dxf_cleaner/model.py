import math
from dataclasses import dataclass, field
from typing import Literal
from shapely.geometry import LinearRing, LineString

Point = tuple[float, float]


@dataclass
class Segment:
    """One piece of a Contour. Keeps its true geometric nature (line or arc)."""
    kind: Literal["line", "arc"]
    start: Point
    end: Point
    center: Point | None = None
    radius: float | None = None
    ccw: bool | None = None


@dataclass
class Contour:
    segments: list[Segment]
    is_closed: bool
    source_layer: str
    source_handle: str

    def to_shapely(self, arc_tolerance: float = 0.02) -> LinearRing | LineString:
        """Discretize for computation only. NEVER use this output to write a DXF file."""
        points = discretize_contour(self, arc_tolerance)
        return LinearRing(points) if self.is_closed else LineString(points)


@dataclass
class Part:
    exterior: Contour
    interiors: list[Contour] = field(default_factory=list)


@dataclass
class Diagnostic:
    code: str
    message: str
    handle: str | None = None


def _scale_point(p: Point, factor: float) -> Point:
    return (p[0] * factor, p[1] * factor)


def scale_contour(contour: Contour, factor: float) -> Contour:
    """Return a new Contour with every coordinate multiplied by `factor`."""
    new_segments = [
        Segment(
            kind=seg.kind,
            start=_scale_point(seg.start, factor),
            end=_scale_point(seg.end, factor),
            center=_scale_point(seg.center, factor) if seg.center is not None else None,
            radius=seg.radius * factor if seg.radius is not None else None,
            ccw=seg.ccw,
        )
        for seg in contour.segments
    ]
    return Contour(
        segments=new_segments,
        is_closed=contour.is_closed,
        source_layer=contour.source_layer,
        source_handle=contour.source_handle,
    )


def discretize_arc(segment: Segment, tolerance: float) -> list[Point]:
    """Sample points along an arc segment so consecutive samples deviate from the
    true arc by at most `tolerance` (chord/sagitta tolerance)."""
    cx, cy = segment.center
    r = segment.radius
    a0 = math.atan2(segment.start[1] - cy, segment.start[0] - cx)
    a1 = math.atan2(segment.end[1] - cy, segment.end[0] - cx)
    two_pi = 2 * math.pi
    if segment.ccw:
        sweep = (a1 - a0) % two_pi
        if sweep == 0:
            sweep = two_pi
    else:
        sweep = -((a0 - a1) % two_pi)
        if sweep == 0:
            sweep = -two_pi

    tol = min(tolerance, r * 0.999)
    max_step = 2 * math.acos(1 - tol / r)
    steps = max(1, math.ceil(abs(sweep) / max_step))

    points: list[Point] = []
    for i in range(steps + 1):
        if i == 0:
            points.append(segment.start)
        elif i == steps:
            points.append(segment.end)
        else:
            a = a0 + sweep * i / steps
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def discretize_contour(contour: Contour, tolerance: float) -> list[Point]:
    """Flatten every segment into a single ordered point list, without duplicating
    the shared vertex between consecutive segments."""
    points: list[Point] = []
    for i, seg in enumerate(contour.segments):
        seg_points = discretize_arc(seg, tolerance) if seg.kind == "arc" else [seg.start, seg.end]
        points.extend(seg_points if i == 0 else seg_points[1:])
    return points
