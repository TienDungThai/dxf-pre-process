"""Click-independent processing logic shared by the CLI and the GUI.

Both front ends call `process_one` / `write_batch_report` / `apply_overrides`
so the actual pipeline-driving logic (and its edge cases, like the batch
report staying consistent when one file in a directory errors out) lives in
exactly one place.
"""
import csv
from pathlib import Path
from typing import Callable

from dxf_cleaner.config import Config
from dxf_cleaner.pipeline import run_pipeline, write_pipeline_result

RASTER_SUFFIXES = {".png", ".jpg", ".jpeg"}

# (level, message) -- level is one of "info", "warning", "critical"
LogFn = Callable[[str, str], None]


def process_one(
    input_path: Path,
    output_path: Path | None,
    config: Config,
    check: bool,
    log: LogFn,
    width_mm: float | None = None,
    height_mm: float | None = None,
    batch_rows: list[dict] | None = None,
    force: bool = False,
) -> int:
    """Run the pipeline on one file, report its outcome via `log`, and return
    its exit code (0 ok, 1 warning, 2 critical)."""
    is_raster = input_path.suffix.lower() in RASTER_SUFFIXES
    preview_path = str(input_path.with_name(f"{input_path.stem}_KIEMTRA.png")) if is_raster else None

    result = run_pipeline(
        str(input_path), config, width_mm=width_mm, height_mm=height_mm, preview_path=preview_path
    )
    report = result.report

    log("info", f"{input_path}: {report.level}")
    for line in report.critical:
        log("critical", f"  CRITICAL: {line}")
    for line in report.warnings:
        log("warning", f"  WARNING: {line}")
    for key, value in sorted(report.info.items()):
        log("info", f"  {key}: {value}")

    if batch_rows is not None:
        batch_rows.append({"file": input_path.name, "status": report.level, **report.info})

    if not check and (report.level != "critical" or force):
        target = output_path if output_path is not None else input_path.with_name(
            f"{input_path.stem}.clean.dxf"
        )
        write_pipeline_result(result, str(target), config)
        if report.level == "critical":
            log("warning", f"  -> wrote {target} DESPITE critical warnings above (--force)")
        else:
            log("info", f"  -> wrote {target}")

    return {"ok": 0, "warning": 1, "critical": 2}[report.level]


def write_batch_report(batch_rows: list[dict], out_dir: Path) -> Path:
    """Write a consolidated BAO-CAO.csv covering every file processed in
    directory mode. Columns are the dynamic union of every row's keys (DXF
    and PNG rows carry different info keys), so a mixed-input batch still
    opens as one consistent spreadsheet -- missing columns are blank."""
    fieldnames = ["file", "status"]
    seen = set(fieldnames)
    for row in batch_rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)

    report_path = out_dir / "BAO-CAO.csv"
    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(batch_rows)
    return report_path


def apply_overrides(config: Config, snap_tol: float | None, weld_mode: str | None, no_simplify: bool) -> Config:
    data = config.model_dump()
    if snap_tol is not None:
        data["snap"]["tolerance"] = snap_tol
    if weld_mode is not None:
        data["weld"]["mode"] = weld_mode
    if no_simplify:
        data["simplify"]["enabled"] = False
    return Config(**data)


def apply_raster_overrides(
    config: Config, px_per_mm: float | None, invert: bool, raster_threshold: int | None
) -> Config:
    raster_data = config.raster.model_dump()
    if px_per_mm is not None:
        raster_data["pixels_per_mm"] = px_per_mm
    if invert:
        raster_data["invert"] = True
    if raster_threshold is not None:
        raster_data["threshold"] = raster_threshold
    return config.model_copy(update={"raster": type(config.raster)(**raster_data)})


def apply_thickness_override(config: Config, thickness: float | None) -> Config:
    if thickness is None:
        return config
    validate_data = config.validate.model_dump()
    validate_data["material_thickness"] = thickness
    return config.model_copy(update={"validate": type(config.validate)(**validate_data)})


def process_path(
    input_path: Path,
    output_path: Path | None,
    config: Config,
    check: bool,
    log: LogFn,
    width_mm: float | None = None,
    height_mm: float | None = None,
    force: bool = False,
) -> tuple[int, Path | None]:
    """Process a single file or a directory of files. Returns (worst exit
    code, batch report path or None). Mirrors the CLI's directory-mode
    behavior: one broken file doesn't discard the report for files that
    already succeeded."""
    if input_path.is_dir():
        if output_path is not None:
            output_path.mkdir(parents=True, exist_ok=True)
        files = sorted(
            f for f in input_path.iterdir()
            if f.suffix.lower() in {".dxf"} | RASTER_SUFFIXES
        )
        if not files:
            log("info", f"No .dxf files found in {input_path}")
            return 0, None
        worst = 0
        batch_rows: list[dict] = []
        for f in files:
            target = (output_path / f"{f.stem}.clean.dxf") if output_path is not None else None
            try:
                worst = max(worst, process_one(f, target, config, check, log, width_mm, height_mm, batch_rows, force))
            except Exception as exc:
                log("critical", f"{f}: System error: {exc}")
                batch_rows.append({"file": f.name, "status": "error"})
                worst = max(worst, 3)
        report_dir = output_path if output_path is not None else input_path
        report_path = write_batch_report(batch_rows, report_dir)
        return worst, report_path
    else:
        code = process_one(input_path, output_path, config, check, log, width_mm, height_mm, force=force)
        return code, None
