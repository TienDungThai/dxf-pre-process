import math
from dxf_cleaner.model import Segment, Contour, Diagnostic, Point


def _round_point(p: Point, ndigits: int = 6) -> Point:
    return (round(p[0], ndigits), round(p[1], ndigits))


def _arc_midpoint(seg: Segment) -> Point:
    """A point on the arc's own sweep (not just its endpoints), so that two
    arcs sharing the same endpoints/center/radius but tracing complementary
    portions of the same circle (e.g. two half-circle arcs) are not mistaken
    for duplicates of each other."""
    cx, cy = seg.center
    r = seg.radius
    a0 = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    a1 = math.atan2(seg.end[1] - cy, seg.end[0] - cx)
    two_pi = 2 * math.pi
    if seg.ccw:
        sweep = (a1 - a0) % two_pi
        if sweep == 0:
            sweep = two_pi
    else:
        sweep = -((a0 - a1) % two_pi)
        if sweep == 0:
            sweep = -two_pi
    a_mid = a0 + sweep / 2
    return (cx + r * math.cos(a_mid), cy + r * math.sin(a_mid))


def _segment_key(seg: Segment, ndigits: int = 6) -> tuple:
    endpoints = frozenset((_round_point(seg.start, ndigits), _round_point(seg.end, ndigits)))
    if seg.kind == "line":
        return ("line", endpoints)
    return (
        "arc", endpoints,
        round(seg.center[0], ndigits), round(seg.center[1], ndigits), round(seg.radius, ndigits),
        _round_point(_arc_midpoint(seg), ndigits),
    )


def _line_group_key(seg: Segment, ndigits: int = 4) -> tuple[float, float, float]:
    dx, dy = seg.end[0] - seg.start[0], seg.end[1] - seg.start[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    if ux < 0 or (ux == 0 and uy < 0):
        ux, uy = -ux, -uy
    perp = seg.start[0] * (-uy) + seg.start[1] * ux
    return (round(ux, ndigits), round(uy, ndigits), round(perp, ndigits))


def dedupe_contours(contours: list[Contour], merge_common_edges: bool) -> tuple[list[Contour], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    segments: list[Segment] = []
    origin_contour: list[int] = []
    for ci, contour in enumerate(contours):
        for seg in contour.segments:
            segments.append(seg)
            origin_contour.append(ci)

    removed = [False] * len(segments)

    groups: dict[tuple, list[int]] = {}
    for i, seg in enumerate(segments):
        groups.setdefault(_segment_key(seg), []).append(i)

    in_dup_group: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        in_dup_group.update(idxs)
        # "Standalone" (case a) means every origin contour touched by this
        # duplicate-key group consists ENTIRELY of segments belonging to the
        # group — i.e. the whole contour is duplicate lines with nothing else
        # going on (the single-segment case is just the size-1 instance of
        # this). If a contour has segments outside the group too, the shared
        # edge is only part of a larger real contour, so it's case (c).
        contour_dup_counts: dict[int, int] = {}
        for i in idxs:
            ci = origin_contour[i]
            contour_dup_counts[ci] = contour_dup_counts.get(ci, 0) + 1
        all_standalone = all(
            count == len(contours[ci].segments) for ci, count in contour_dup_counts.items()
        )
        s0 = segments[idxs[0]]
        if all_standalone:
            for d in idxs[1:]:
                removed[d] = True
                diagnostics.append(Diagnostic(
                    code="DUPLICATE_SEGMENT_REMOVED",
                    message=f"Removed duplicate segment at {segments[d].start}-{segments[d].end}",
                ))
        else:
            diagnostics.append(Diagnostic(
                code="COMMON_EDGE",
                message=(
                    f"Shared edge at {s0.start}-{s0.end} merged (merge_common_edges enabled)"
                    if merge_common_edges else
                    f"Shared edge at {s0.start}-{s0.end} kept (common-line cutting)"
                ),
            ))
            if merge_common_edges:
                for d in idxs[1:]:
                    removed[d] = True

    line_groups: dict[tuple, list[int]] = {}
    for i, seg in enumerate(segments):
        if removed[i] or seg.kind != "line" or i in in_dup_group:
            continue
        line_groups.setdefault(_line_group_key(seg), []).append(i)

    for key, idxs in line_groups.items():
        if len(idxs) < 2:
            continue
        ux, uy = key[0], key[1]
        intervals = []
        for i in idxs:
            seg = segments[i]
            t0 = seg.start[0] * ux + seg.start[1] * uy
            t1 = seg.end[0] * ux + seg.end[1] * uy
            intervals.append((min(t0, t1), max(t0, t1), i))
        intervals.sort()

        cluster: list[int] = [intervals[0][2]]
        cur_lo, cur_hi = intervals[0][0], intervals[0][1]
        for lo, hi, i in intervals[1:]:
            if lo < cur_hi:  # strict: genuine overlap only, not mere endpoint-adjacency
                cur_hi = max(cur_hi, hi)
                cluster.append(i)
            else:
                _finalize_overlap_cluster(cluster, segments, removed, diagnostics, ux, uy, cur_lo, cur_hi)
                cluster, cur_lo, cur_hi = [i], lo, hi
        _finalize_overlap_cluster(cluster, segments, removed, diagnostics, ux, uy, cur_lo, cur_hi)

    result: list[Contour] = []
    for ci, contour in enumerate(contours):
        kept = [seg for i, seg in enumerate(segments) if origin_contour[i] == ci and not removed[i]]
        if kept:
            result.append(Contour(
                segments=kept, is_closed=contour.is_closed,
                source_layer=contour.source_layer, source_handle=contour.source_handle,
            ))
    return result, diagnostics


def _finalize_overlap_cluster(
    member_idxs: list[int], segments: list[Segment], removed: list[bool],
    diagnostics: list[Diagnostic], ux: float, uy: float, lo: float, hi: float,
) -> None:
    if len(member_idxs) < 2:
        return
    keep_idx = member_idxs[0]
    start, end = (lo * ux, lo * uy), (hi * ux, hi * uy)
    segments[keep_idx] = Segment(kind="line", start=start, end=end)
    for i in member_idxs[1:]:
        removed[i] = True
    diagnostics.append(Diagnostic(
        code="OVERLAPPING_SEGMENTS_MERGED",
        message=f"Merged {len(member_idxs)} overlapping collinear segments into {start}-{end}",
    ))
