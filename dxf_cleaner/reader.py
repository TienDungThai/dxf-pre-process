from dxf_cleaner.config import Config
from dxf_cleaner.model import Diagnostic

_SUPPORTED_UNIT_SCALES = {1: 25.4, 4: 1.0}
_ASSUMED_UNIT_SCALES = {"mm": 1.0, "inch": 25.4}


def determine_unit_scale(doc, config: Config) -> tuple[float, Diagnostic | None]:
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits in _SUPPORTED_UNIT_SCALES:
        return _SUPPORTED_UNIT_SCALES[insunits], None
    if insunits == 0:
        scale = _ASSUMED_UNIT_SCALES[config.input.assumed_unit]
        return scale, Diagnostic(
            code="ASSUMED_UNIT",
            message=f"$INSUNITS not set in file; assuming {config.input.assumed_unit} per config",
        )
    return 1.0, Diagnostic(
        code="UNSUPPORTED_INSUNITS",
        message=f"$INSUNITS={insunits} is not supported (only mm/inch); treating as mm",
    )
