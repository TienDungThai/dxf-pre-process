from dataclasses import dataclass
from pathlib import Path

from dxf_cleaner.config import Config
from dxf_cleaner.model import Part, Contour, Diagnostic
from dxf_cleaner.reader import read_dxf
from dxf_cleaner.raster import read_raster, render_preview
from dxf_cleaner.writer import write_dxf
from dxf_cleaner.stages.snap import snap_and_chain
from dxf_cleaner.stages.dedupe import dedupe_contours
from dxf_cleaner.stages.despeckle import despeckle_contours
from dxf_cleaner.stages.weld import weld_contours
from dxf_cleaner.stages.hierarchy import build_hierarchy
from dxf_cleaner.stages.simplify import simplify_contours
from dxf_cleaner.stages.validate import validate, ValidationReport


@dataclass
class PipelineResult:
    parts: list[Part]
    diagnostics: list[Diagnostic]
    report: ValidationReport


def parts_to_contours(parts: list[Part]) -> list[Contour]:
    contours: list[Contour] = []
    for part in parts:
        contours.append(part.exterior)
        contours.extend(part.interiors)
    return contours


_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _render_raster_preview(parts: list[Part], preview_data: dict, preview_path: str) -> list[Diagnostic]:
    """Render the _KIEMTRA.png preview AFTER build_hierarchy has classified
    exterior vs. hole, so each ring is drawn in its correct color -- unlike
    read_raster (which runs before hierarchy classification exists), this
    knows exactly which contours are holes.

    A contour whose source_handle has no entry in rings_px_by_handle (most
    commonly: despeckle removed it, or weld_contours re-emitted it under a
    NEW handle such as "WELD_<a>_<b>") has no traced ring to draw -- but that
    contour is still very much present in the cleaned DXF output. Silently
    leaving it out of the "mandatory pre-cut check" image would be
    misleading, so any such gap is reported back as a diagnostic instead of
    being swallowed."""
    rings_px_by_handle = preview_data["rings_px_by_handle"]
    ordered_rings = []
    holes_by_ring = []
    missing_count = 0
    for part in parts:
        for contour, is_hole in [(part.exterior, False)] + [(hole, True) for hole in part.interiors]:
            ring_px = rings_px_by_handle.get(contour.source_handle)
            if ring_px is not None:
                ordered_rings.append(ring_px)
                holes_by_ring.append(is_hole)
            else:
                missing_count += 1

    render_preview(
        preview_data["mask"], ordered_rings, holes_by_ring,
        preview_data["dist"], preview_data["skel"], preview_data["thin_threshold_px"],
        preview_path,
    )

    if missing_count == 0:
        return []
    return [Diagnostic(
        code="PREVIEW_INCOMPLETE",
        message=(
            f"{missing_count} boundary(ies) could not be matched back to a traced ring "
            f"(commonly caused by weld merging raster contours under a new handle) and "
            f"are missing from {preview_path} -- do not rely on the preview image alone "
            f"for hole/exterior verification on this file"
        ),
    )]


def run_pipeline(
    input_path: str,
    config: Config,
    *,
    width_mm: float | None = None,
    height_mm: float | None = None,
    preview_path: str | None = None,
) -> PipelineResult:
    if Path(input_path).suffix.lower() in _RASTER_SUFFIXES:
        read_result = read_raster(input_path, config, width_mm=width_mm, height_mm=height_mm)
    else:
        read_result = read_dxf(input_path, config)
    diagnostics: list[Diagnostic] = list(read_result.diagnostics)

    contour_count_before = len(read_result.contours)
    node_count_before = sum(len(c.segments) for c in read_result.contours)

    contours, snap_diags = snap_and_chain(
        read_result.contours, config.snap.tolerance, config.snap.max_reportable_gap
    )
    diagnostics.extend(snap_diags)
    closed_count = sum(1 for c in contours if c.is_closed)

    dedup_count = 0
    if config.dedupe.enabled:
        contours, dedupe_diags = dedupe_contours(contours, config.dedupe.merge_common_edges)
        diagnostics.extend(dedupe_diags)
        dedup_count = sum(
            1 for d in dedupe_diags if d.code in ("DUPLICATE_SEGMENT_REMOVED", "OVERLAPPING_SEGMENTS_MERGED")
        )

    contours, despeckle_diags = despeckle_contours(
        contours, config.despeckle.min_perimeter, config.despeckle.min_area
    )
    diagnostics.extend(despeckle_diags)

    contours, weld_diags, welded_handles = weld_contours(contours, config.weld.mode)
    diagnostics.extend(weld_diags)
    weld_count = sum(1 for d in weld_diags if d.code == "CONTOURS_WELDED")

    parts, hierarchy_diags = build_hierarchy(contours)
    diagnostics.extend(hierarchy_diags)

    if read_result.raster_preview_data is not None and preview_path is not None:
        diagnostics.extend(_render_raster_preview(parts, read_result.raster_preview_data, preview_path))

    if config.simplify.enabled:
        touched_handles = read_result.flattened_handles | welded_handles
        flat_contours = parts_to_contours(parts)
        simplified, simplify_diags = simplify_contours(
            flat_contours, touched_handles, config.simplify.tolerance,
            config.simplify.collinear_angle_deg, config.simplify.max_area_deviation_pct,
        )
        diagnostics.extend(simplify_diags)
        # `simplify_contours` returns exactly one output contour per input
        # contour, in the same order it was given (see simplify_contours).
        # `flat_contours` was built by walking `part.exterior` then
        # `part.interiors` for each part, in order (see parts_to_contours),
        # so we can consume `simplified` positionally in that same
        # interleaving to reassign each part's contours. A handle-keyed
        # lookup would silently collapse when two contours share a
        # source_handle (e.g. two chains split from one AMBIGUOUS_JUNCTION
        # entity), swapping one part's geometry for another's.
        assert len(simplified) == len(flat_contours)
        it = iter(simplified)
        for part in parts:
            part.exterior = next(it)
            part.interiors = [next(it) for _ in part.interiors]

    contour_count_after = len(parts_to_contours(parts))
    node_count_after = sum(len(c.segments) for c in parts_to_contours(parts))

    stats = dict(
        contour_count_before=contour_count_before,
        contour_count_after=contour_count_after,
        node_count_before=node_count_before,
        node_count_after=node_count_after,
        dedup_count=dedup_count,
        closed_count=closed_count,
        weld_count=weld_count,
    )
    if read_result.raster_stats is not None:
        stats.update(read_result.raster_stats)
    report = validate(parts, diagnostics, config.validate, stats)

    return PipelineResult(parts=parts, diagnostics=diagnostics, report=report)


def write_pipeline_result(result: PipelineResult, output_path: str, config: Config) -> None:
    write_dxf(parts_to_contours(result.parts), output_path, config)
