from dataclasses import dataclass, field
from typing import Literal

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
