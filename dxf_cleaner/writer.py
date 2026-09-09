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


def _write_lwpolyline(msp, contour: Contour, layer: str) -> None:
    vertices = []
    for seg in contour.segments:
        bulge = _arc_bulge(seg) if seg.kind == "arc" else 0.0
        vertices.append((seg.start[0], seg.start[1], 0, 0, bulge))
    if not contour.is_closed:
        last = contour.segments[-1]
        vertices.append((last.end[0], last.end[1], 0, 0, 0.0))
    msp.add_lwpolyline(vertices, format="xyseb", close=contour.is_closed, dxfattribs={"layer": layer})


def write_dxf(contours: list[Contour], path: str, config: Config) -> None:
    doc = ezdxf.new(config.output.dxf_version)
    doc.header["$INSUNITS"] = 4

    layer_name = config.output.layer_name
    if layer_name not in doc.layers:
        doc.layers.add(name=layer_name, color=7)

    msp = doc.modelspace()
    for contour in contours:
        if contour_as_full_circle(contour) is not None:
            _write_circle(msp, contour, layer_name)
        else:
            _write_lwpolyline(msp, contour, layer_name)

    if contours:
        minxs, minys, maxxs, maxys = zip(*(contour_bbox(c) for c in contours))
        extmin = (min(minxs), min(minys), 0.0)
        extmax = (max(maxxs), max(maxys), 0.0)
        msp.dxf.extmin = extmin
        msp.dxf.extmax = extmax
        doc.header["$EXTMIN"] = extmin
        doc.header["$EXTMAX"] = extmax

    doc.saveas(path)
