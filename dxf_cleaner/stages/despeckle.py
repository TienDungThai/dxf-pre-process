import math
from dxf_cleaner.model import Contour, Diagnostic, contour_bbox, contour_signed_area, discretize_contour


def _contour_perimeter(contour: Contour, arc_tolerance: float = 0.02) -> float:
    points = discretize_contour(contour, arc_tolerance)
    n = len(points)
    return sum(math.dist(points[i], points[(i + 1) % n]) for i in range(n))


def despeckle_contours(
    contours: list[Contour], min_perimeter: float, min_area: float
) -> tuple[list[Contour], list[Diagnostic]]:
    kept: list[Contour] = []
    diagnostics: list[Diagnostic] = []
    for contour in contours:
        if not contour.is_closed:
            kept.append(contour)
            continue
        perimeter = _contour_perimeter(contour)
        area = abs(contour_signed_area(contour))
        if perimeter < min_perimeter or area < min_area:
            diagnostics.append(Diagnostic(
                code="DESPECKLED",
                message=(
                    f"Removed tiny contour (perimeter={perimeter:.4f}mm, area={area:.4f}mm2) "
                    f"at bbox={contour_bbox(contour)}"
                ),
                handle=contour.source_handle,
            ))
            continue
        kept.append(contour)
    return kept, diagnostics
