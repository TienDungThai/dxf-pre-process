from typing import Literal
import yaml
from pydantic import BaseModel, Field


class InputConfig(BaseModel):
    assumed_unit: Literal["mm", "inch"] = "mm"


class FlattenConfig(BaseModel):
    chord_tolerance: float = 0.02
    detect_circular_splines: bool = True


class SnapConfig(BaseModel):
    tolerance: float = 0.05
    max_reportable_gap: float = 2.0


class DedupeConfig(BaseModel):
    enabled: bool = True
    merge_common_edges: bool = False


class DespeckleConfig(BaseModel):
    min_perimeter: float = 0.15
    min_area: float = 0.02


class WeldConfig(BaseModel):
    mode: Literal["off", "overlapping", "all"] = "overlapping"


class SimplifyConfig(BaseModel):
    enabled: bool = True
    tolerance: float = 0.01
    collinear_angle_deg: float = 0.1
    max_area_deviation_pct: float = 0.1


class ValidateConfig(BaseModel):
    sheet_width: float = 1500
    sheet_height: float = 3000
    material_thickness: float = 2.0
    kerf_width: float = 0.15
    min_hole_diameter_ratio: float = 1.0
    min_cut_width: float = 0.7


class OutputConfig(BaseModel):
    dxf_version: str = "AC1015"
    layer_name: str = "CUT"
    preserve_arcs: bool = True


class RasterConfig(BaseModel):
    pixels_per_mm: float | None = Field(default=None, gt=0)
    threshold: int | None = None
    invert: bool = False
    min_area_px: float = 20.0
    smooth_sigma: float = 0.4
    prune_mm: float = 5.0
    circle_fit_tolerance_mm: float = 0.12
    circle_fit_tolerance_px: float = 1.5


class Config(BaseModel):
    input: InputConfig = InputConfig()
    flatten: FlattenConfig = FlattenConfig()
    snap: SnapConfig = SnapConfig()
    dedupe: DedupeConfig = DedupeConfig()
    despeckle: DespeckleConfig = DespeckleConfig()
    weld: WeldConfig = WeldConfig()
    simplify: SimplifyConfig = SimplifyConfig()
    validate: ValidateConfig = ValidateConfig()
    output: OutputConfig = OutputConfig()
    raster: RasterConfig = RasterConfig()


def load_config(path: str | None) -> Config:
    if path is None:
        return Config()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(**data)
