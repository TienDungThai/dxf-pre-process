from typing import Literal
import yaml
from pydantic import BaseModel


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
    min_perimeter: float = 0.5
    min_area: float = 0.1


class ValidateConfig(BaseModel):
    sheet_width: float = 1500
    sheet_height: float = 3000
    material_thickness: float = 2.0
    kerf_width: float = 0.15
    min_hole_diameter_ratio: float = 1.0


class OutputConfig(BaseModel):
    dxf_version: str = "AC1015"
    layer_name: str = "CUT"
    preserve_arcs: bool = True


class Config(BaseModel):
    input: InputConfig = InputConfig()
    flatten: FlattenConfig = FlattenConfig()
    snap: SnapConfig = SnapConfig()
    dedupe: DedupeConfig = DedupeConfig()
    despeckle: DespeckleConfig = DespeckleConfig()
    validate: ValidateConfig = ValidateConfig()
    output: OutputConfig = OutputConfig()


def load_config(path: str | None) -> Config:
    if path is None:
        return Config()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(**data)
