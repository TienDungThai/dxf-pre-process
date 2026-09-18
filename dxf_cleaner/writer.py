import math
import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.model import Contour, Segment, contour_as_full_circle, contour_bbox


def _sweep_angle(seg: Segment) -> float:
    """The arc's swept angle in [0, 2*pi), derived from start/end/center/ccw."""
    cx, cy = seg.center
    a_start = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    a_end = math.atan2(seg.end[1] - cy, seg.end[0] - cx)
    two_pi = 2 * math.pi
    if seg.ccw:
        return (a_end - a_start) % two_pi
    return (a_start - a_end) % two_pi


def _arc_bulge(seg: Segment) -> float:
    theta = _sweep_angle(seg)
    magnitude = math.tan(theta / 4)
    return magnitude if seg.ccw else -magnitude


def _write_circle(msp, contour: Contour, layer: str) -> None:
    center, radius = contour_as_full_circle(contour)
    msp.add_circle(center=center, radius=radius, dxfattribs={"layer": layer})


_FULL_SWEEP_TOL = 1e-9
# How close (in radians) the swept angle may get to a full 2*pi turn before
# tan(theta/4) is treated as unsafe to write directly (it diverges at
# theta == 2*pi). 1e-6 rad at a 1000mm radius arc is a ~1um positional error,
# far below any laser/CNC tolerance, so treating it as a full sweep is safe.
_FULL_SWEEP_ANGLE_TOL = 1e-6


def _split_full_sweep_arcs(segments: list[Segment]) -> list[Segment]:
    """Split any near-full-sweep arc into two half-circle arcs.

    A full-sweep arc (start == end exactly, i.e. a whole circle encoded as one
    arc segment) reduces its modulo-wrapped angle to 0, and `tan(theta/4)`
    would then write it as a zero-length straight line. A NEARLY-full sweep
    (start and end a hair apart -- e.g. after snap/weld floating-point
    rounding) is worse: depending on which side the rounding error falls,
    the wrapped angle can come out just *under* 2*pi instead of just over 0,
    and `tan(theta/4)` explodes toward the asymptote at theta == 2*pi,
    writing a huge/garbage bulge into the DXF. Both cases are handled the
    same way CIRCLE entities and the spline circular-detection already
    represent a full circle (two half-circle arcs from the start point),
    so the bulge of each half is always well defined (+/-1) regardless of
    which way the original near-zero chord rounded.
    """
    out: list[Segment] = []
    for seg in segments:
        if seg.kind != "arc" or seg.center is None or seg.radius is None:
            out.append(seg)
            continue
        is_exact_closure = math.dist(seg.start, seg.end) < _FULL_SWEEP_TOL
        is_near_full_turn = _sweep_angle(seg) > (2 * math.pi - _FULL_SWEEP_ANGLE_TOL)
        if is_exact_closure or is_near_full_turn:
            cx, cy = seg.center
            a0 = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
            a_mid = a0 + (math.pi if seg.ccw else -math.pi)
            p_mid = (cx + seg.radius * math.cos(a_mid), cy + seg.radius * math.sin(a_mid))
            out.append(Segment(kind="arc", start=seg.start, end=p_mid,
                               center=seg.center, radius=seg.radius, ccw=seg.ccw))
            out.append(Segment(kind="arc", start=p_mid, end=seg.end,
                               center=seg.center, radius=seg.radius, ccw=seg.ccw))
        else:
            out.append(seg)
    return out


def _write_lwpolyline(msp, contour: Contour, layer: str) -> None:
    segments = _split_full_sweep_arcs(contour.segments)
    vertices = []
    for seg in segments:
        bulge = _arc_bulge(seg) if seg.kind == "arc" else 0.0
        vertices.append((seg.start[0], seg.start[1], 0, 0, bulge))
    if not contour.is_closed:
        last = segments[-1]
        vertices.append((last.end[0], last.end[1], 0, 0, 0.0))
    msp.add_lwpolyline(vertices, format="xyseb", close=contour.is_closed, dxfattribs={"layer": layer})


def write_dxf(contours: list[Contour], path: str, config: Config) -> None:
    doc = ezdxf.new(config.output.dxf_version)
    doc.header["$INSUNITS"] = 4

    layer_name = config.output.layer_name
    if layer_name not in doc.layers:
        doc.layers.add(name=layer_name, color=7)

    msp = doc.modelspace()
    writable = [c for c in contours if c.segments]
    for contour in writable:
        # Fail loudly rather than silently dropping vertices (see Contour docstring).
        # Re-raise with the offending contour attributed, so a bug upstream is
        # traceable instead of surfacing as a bare mid-write ValueError.
        try:
            contour.assert_contiguous()
        except ValueError as exc:
            raise ValueError(
                f"Refusing to write non-contiguous contour (layer={contour.source_layer!r}, "
                f"handle={contour.source_handle!r}, {len(contour.segments)} segments, "
                f"is_closed={contour.is_closed}) to {path!r}: {exc}"
            ) from exc
        if contour_as_full_circle(contour) is not None:
            _write_circle(msp, contour, layer_name)
        else:
            _write_lwpolyline(msp, contour, layer_name)

    if writable:
        minxs, minys, maxxs, maxys = zip(*(contour_bbox(c) for c in writable))
        extmin = (min(minxs), min(minys), 0.0)
        extmax = (max(maxxs), max(maxys), 0.0)
        msp.dxf.extmin = extmin
        msp.dxf.extmax = extmax
        doc.header["$EXTMIN"] = extmin
        doc.header["$EXTMAX"] = extmax

    doc.saveas(path)
