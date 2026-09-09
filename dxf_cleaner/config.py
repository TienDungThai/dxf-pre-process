from typing import Literal
import yaml
from pydantic import BaseModel


class InputConfig(BaseModel):
    assumed_unit: Literal["mm", "inch"] = "mm"


class FlattenConfig(BaseModel):
    chord_tolerance: float = 0.02
    detect_circular_splines: bool = True


class OutputConfig(BaseModel):
    dxf_version: str = "AC1015"
    layer_name: str = "CUT"
    preserve_arcs: bool = True


class Config(BaseModel):
    input: InputConfig = InputConfig()
    flatten: FlattenConfig = FlattenConfig()
    output: OutputConfig = OutputConfig()


def load_config(path: str | None) -> Config:
    if path is None:
        return Config()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(**data)
