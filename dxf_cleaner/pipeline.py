from dataclasses import dataclass

from dxf_cleaner.config import Config
from dxf_cleaner.model import Part, Contour, Diagnostic
from dxf_cleaner.reader import read_dxf
from dxf_cleaner.writer import write_dxf
from dxf_cleaner.stages.snap import snap_and_chain
from dxf_cleaner.stages.dedupe import dedupe_contours
from dxf_cleaner.stages.despeckle import despeckle_contours
from dxf_cleaner.stages.hierarchy import build_hierarchy
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


def run_pipeline(input_path: str, config: Config) -> PipelineResult:
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

    parts, hierarchy_diags = build_hierarchy(contours)
    diagnostics.extend(hierarchy_diags)

    contour_count_after = len(parts_to_contours(parts))
    node_count_after = sum(len(c.segments) for c in parts_to_contours(parts))

    stats = dict(
        contour_count_before=contour_count_before,
        contour_count_after=contour_count_after,
        node_count_before=node_count_before,
        node_count_after=node_count_after,
        dedup_count=dedup_count,
        closed_count=closed_count,
        weld_count=0,  # weld.py doesn't exist until Phase 3
    )
    report = validate(parts, diagnostics, config.validate, stats)

    return PipelineResult(parts=parts, diagnostics=diagnostics, report=report)


def write_pipeline_result(result: PipelineResult, output_path: str, config: Config) -> None:
    write_dxf(parts_to_contours(result.parts), output_path, config)
