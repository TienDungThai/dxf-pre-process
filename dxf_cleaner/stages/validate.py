import math
from dataclasses import dataclass, field
from typing import Literal
from shapely.geometry import Polygon

from dxf_cleaner.model import Part, Diagnostic, contour_bbox
from dxf_cleaner.config import ValidateConfig

_DROPPED_ENTITY_CODES = {"TEXT_SKIPPED", "3D_ENTITY_SKIPPED", "DEGENERATE_ENTITY_SKIPPED", "ENTITY_CONVERSION_FAILED"}
_CRITICAL_CODES = {"OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY", "POLYGON_UNRECOVERABLE", "OPEN_GAP"}


@dataclass
class ValidationReport:
    level: Literal["ok", "warning", "critical"]
    critical: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, int | float] = field(default_factory=dict)


def _hole_diameter(part_hole_poly: Polygon) -> float:
    return 2 * math.sqrt(abs(part_hole_poly.area) / math.pi)


def validate(
    parts: list[Part], diagnostics: list[Diagnostic], config: ValidateConfig, stats: dict[str, int]
) -> ValidationReport:
    critical: list[str] = []
    warnings: list[str] = []

    diag_codes = [d.code for d in diagnostics]
    for code in diag_codes:
        if code in _CRITICAL_CODES:
            critical.append(f"{code}: see diagnostics for details")
        elif code in _DROPPED_ENTITY_CODES:
            warnings.append(f"Entity dropped ({code})")
        elif code == "ASSUMED_UNIT":
            warnings.append("Input file did not specify units; assumed unit was used")
        elif code == "AMBIGUOUS_JUNCTION":
            warnings.append("Ambiguous junction encountered while reconnecting contours")

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

    all_geoms: list[tuple[str, Polygon]] = []
    for part in parts:
        all_geoms.append((part.exterior.source_handle, Polygon(part.exterior.to_shapely())))
        for hole in part.interiors:
            all_geoms.append((hole.source_handle, Polygon(hole.to_shapely())))
            diameter = _hole_diameter(all_geoms[-1][1])
            min_diameter = config.min_hole_diameter_ratio * config.material_thickness
            if diameter < min_diameter:
                warnings.append(
                    f"Hole {hole.source_handle!r} diameter {diameter:.3f}mm is smaller than "
                    f"the material-thickness-derived minimum {min_diameter:.3f}mm"
                )

    for part_a in parts:
        for part_b in parts:
            if part_a is part_b or id(part_a) >= id(part_b):
                continue
            poly_a = Polygon(part_a.exterior.to_shapely())
            poly_b = Polygon(part_b.exterior.to_shapely())
            if abs(poly_a.area - poly_b.area) < 1e-6 and poly_a.equals(poly_b):
                warnings.append(
                    f"Parts {part_a.exterior.source_handle!r} and {part_b.exterior.source_handle!r} "
                    f"are fully duplicate (same shape, same position)"
                )

    min_gap = 2 * config.kerf_width
    for i, (name_a, geom_a) in enumerate(all_geoms):
        for name_b, geom_b in all_geoms[i + 1:]:
            if geom_a.distance(geom_b) < min_gap and not geom_a.equals(geom_b):
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
    return ValidationReport(level=level, critical=critical, warnings=warnings, info=info)
