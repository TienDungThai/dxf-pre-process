import math
import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.model import Contour, Segment, contour_as_full_circle, contour_bbox


def _arc_bulge(seg: Segment) -> float:
    cx, cy = seg.center
    a_start = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    a_end = math.atan2(seg.end[1] - cy, seg.end[0] - cx)
    two_pi = 2 * math.pi
    if seg.ccw:
        theta = (a_end - a_start) % two_pi
    else:
        theta = (a_start - a_end) % two_pi
    magnitude = math.tan(theta / 4)
    return magnitude if seg.ccw else -magnitude


def _write_circle(msp, contour: Contour, layer: str) -> None:
    center, radius = contour_as_full_circle(contour)
    msp.add_circle(center=center, radius=radius, dxfattribs={"layer": layer})


_FULL_SWEEP_TOL = 1e-9


def _split_full_sweep_arcs(segments: list[Segment]) -> list[Segment]:
    """Split any full-sweep arc (start == end, i.e. a whole circle encoded as one
    arc segment) into two half-circle arcs.

    A full-sweep arc has a bulge angle of 2*pi, and `tan(theta/4)` computed from
    the modulo-reduced angle collapses to 0 -- which would write the arc as a
    zero-length straight line. Splitting matches how CIRCLE entities and the
    spline circular-detection already represent a full circle (two arcs), so the
    bulge of each half is well defined (+/-1).
    """
    out: list[Segment] = []
    for seg in segments:
        if (
            seg.kind == "arc"
            and seg.center is not None
            and seg.radius is not None
            and math.dist(seg.start, seg.end) < _FULL_SWEEP_TOL
        ):
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
        contour.assert_contiguous()
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
