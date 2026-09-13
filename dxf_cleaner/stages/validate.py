import math
from dataclasses import dataclass, field
from typing import Literal
from shapely.geometry import Polygon

from dxf_cleaner.model import Part, Diagnostic, contour_bbox
from dxf_cleaner.config import ValidateConfig

_DROPPED_ENTITY_CODES = {"TEXT_SKIPPED", "3D_ENTITY_SKIPPED", "DEGENERATE_ENTITY_SKIPPED", "ENTITY_CONVERSION_FAILED"}
_CRITICAL_CODES = {
    "OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY",
    "POLYGON_UNRECOVERABLE",
    "OPEN_GAP",
    # Same underlying problem as OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY: a shape
    # that should have been closed no longer is.
    "CONTOUR_OPENED_BY_DEDUPE",
}
_WARNING_CODES = {
    # A self-intersecting shape was silently repaired -- the operator should know.
    "INVALID_POLYGON_FIXED",
    "3D_POLYLINE_PROJECTED",
    "UNSUPPORTED_INSUNITS",
    # Simplify skipped a contour because it would have deviated area beyond the
    # configured limit -- the operator should know it kept the denser original.
    "SIMPLIFY_REVERTED_AREA_DEVIATION",
    # weld mode "all" unions every closed contour together, including a
    # hole/island fully contained inside another contour -- the containment
    # is silently absorbed by unary_union, so the operator should know.
    "HOLES_LOST_IN_WELD_ALL",
    # Raster-input diagnostics: computed numbers worth surfacing (actual DPI,
    # fill/border ratios) rather than silently dropped.
    "RASTER_LOW_DPI",
    "RASTER_POSSIBLE_INVERTED",
}
# Warning codes whose Diagnostic.message already contains the useful,
# computed detail (e.g. the actual DPI value) -- surface that message
# directly instead of the generic "see diagnostics for details" text.
_WARNING_CODES_WITH_OWN_MESSAGE = {
    "RASTER_LOW_DPI",
    "RASTER_POSSIBLE_INVERTED",
}
# Codes that are normal, expected cleanup actions. They are deliberately NOT
# escalated to warnings; they are counted into the report's info block so they
# stay visible (e.g. DESPECKLED makes a removed tiny hole auditable even though
# it is gone before validate()'s hole-size check runs).
_INFO_CODES = {
    "DESPECKLED",
    "DUPLICATE_SEGMENT_REMOVED",
    "OVERLAPPING_SEGMENTS_MERGED",
    "COMMON_EDGE",
    "CONTOURS_WELDED",
    # A segment collapsed to a point after endpoint snapping (common with
    # raster-traced, sub-pixel input) and was dropped before it could crash
    # downstream length-based math -- normal, expected cleanup.
    "ZERO_LENGTH_SEGMENT_REMOVED",
}


@dataclass
class ValidationReport:
    level: Literal["ok", "warning", "critical"]
    critical: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, int | float] = field(default_factory=dict)


def _hole_diameter(part_hole_poly: Polygon) -> float:
    return 2 * math.sqrt(abs(part_hole_poly.area) / math.pi)


def validate(
    parts: list[Part], diagnostics: list[Diagnostic], config: ValidateConfig, stats: dict[str, int | float]
) -> ValidationReport:
    critical: list[str] = []
    warnings: list[str] = []

    diag_codes = [d.code for d in diagnostics]
    for diag in diagnostics:
        code = diag.code
        if code in _CRITICAL_CODES:
            critical.append(f"{code}: see diagnostics for details")
        elif code in _DROPPED_ENTITY_CODES:
            warnings.append(f"Entity dropped ({code})")
        elif code == "ASSUMED_UNIT":
            warnings.append("Input file did not specify units; assumed unit was used")
        elif code == "AMBIGUOUS_JUNCTION":
            warnings.append("Ambiguous junction encountered while reconnecting contours")
        elif code in _WARNING_CODES_WITH_OWN_MESSAGE:
            warnings.append(f"{code}: {diag.message}")
        elif code in _WARNING_CODES:
            warnings.append(f"{code}: see diagnostics for details")

    if not parts:
        critical.append("Output is empty: no parts survived processing")

    for part in parts:
        minx, miny, maxx, maxy = contour_bbox(part.exterior)
        width, height = maxx - minx, maxy - miny
        if width > config.sheet_width or height > config.sheet_height:
            critical.append(
                f"Part {part.exterior.source_handle!r} ({width:.1f}x{height:.1f}mm) is oversized "
                f"for the configured sheet ({config.sheet_width}x{config.sheet_height}mm)"
            )

    all_geoms: list[tuple[str, Polygon, int]] = []
    for part_index, part in enumerate(parts):
        all_geoms.append((part.exterior.source_handle, Polygon(part.exterior.to_shapely()), part_index))
        for hole in part.interiors:
            all_geoms.append((hole.source_handle, Polygon(hole.to_shapely()), part_index))
            diameter = _hole_diameter(all_geoms[-1][1])
            min_diameter = config.min_hole_diameter_ratio * config.material_thickness
            if diameter < min_diameter:
                warnings.append(
                    f"Hole {hole.source_handle!r} diameter {diameter:.3f}mm is smaller than "
                    f"the material-thickness-derived minimum {min_diameter:.3f}mm"
                )

    min_feature_width_mm = stats.get("min_feature_width_mm")
    if min_feature_width_mm is not None:
        thickness = config.material_thickness
        if min_feature_width_mm < thickness:
            critical.append(
                f"Thinnest feature {min_feature_width_mm:.2f}mm is narrower than the "
                f"material thickness {thickness:.2f}mm -- cannot cut"
            )
        elif min_feature_width_mm < 2 * thickness:
            warnings.append(
                f"Thinnest feature {min_feature_width_mm:.2f}mm is narrower than 2x the "
                f"material thickness ({2 * thickness:.2f}mm) -- risk of warping/burn"
            )

    n_parts = stats.get("n_parts")
    if n_parts is not None and n_parts > 1:
        warnings.append(
            f"{n_parts} separate parts detected -- consider placing a micro joint per "
            f"part in CypCut before cutting"
        )

    part_polygons = [Polygon(part.exterior.to_shapely()) for part in parts]
    for i, part_a in enumerate(parts):
        poly_a = part_polygons[i]
        for j in range(i + 1, len(parts)):
            part_b, poly_b = parts[j], part_polygons[j]
            if abs(poly_a.area - poly_b.area) < 1e-6 and poly_a.equals(poly_b):
                warnings.append(
                    f"Parts {part_a.exterior.source_handle!r} and {part_b.exterior.source_handle!r} "
                    f"are fully duplicate (same shape, same position)"
                )

    # A distance of ~0 between two DIFFERENT parts' boundaries is an
    # intentional shared (common-cut) edge, not a clearance problem -- so only
    # positive-but-too-small distances are flagged.
    min_gap = 2 * config.kerf_width
    _ZERO_DISTANCE_TOL = 1e-9
    for i, (name_a, geom_a, part_index_a) in enumerate(all_geoms):
        for name_b, geom_b, part_index_b in all_geoms[i + 1:]:
            if part_index_a == part_index_b:
                continue
            # distance() is 0 both for a legitimate shared edge (zero-area
            # intersection) and for two shapes that genuinely overlap, so the
            # near-zero spacing exemption alone would silence real overlaps.
            # `overlaps()` is exactly the predicate wanted here: positive-area
            # interior intersection with NEITHER shape containing the other --
            # so touching edges (zero area) and legitimate nesting (an island
            # part inside another part's hole) are both excluded.
            if geom_a.overlaps(geom_b):
                warnings.append(
                    f"{name_a!r} and {name_b!r} geometrically overlap "
                    f"(intersection area {geom_a.intersection(geom_b).area:.3f}mm2) -- "
                    f"this is likely a design error"
                )
                continue
            distance = geom_a.distance(geom_b)
            if distance <= _ZERO_DISTANCE_TOL:
                continue
            if distance < min_gap and not geom_a.equals(geom_b):
                warnings.append(
                    f"{name_a!r} and {name_b!r} are closer than 2x kerf width ({min_gap}mm)"
                )

    if critical:
        level = "critical"
    elif warnings:
        level = "warning"
    else:
        level = "ok"

    info = dict(stats)
    info["part_count"] = len(parts)
    for code in sorted(_INFO_CODES):
        count = sum(1 for c in diag_codes if c == code)
        if count:
            info[f"diag_{code.lower()}"] = count
    return ValidationReport(level=level, critical=critical, warnings=warnings, info=info)
