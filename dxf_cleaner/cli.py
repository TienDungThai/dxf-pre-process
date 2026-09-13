import csv
from pathlib import Path

import click

from dxf_cleaner.config import Config, load_config
from dxf_cleaner.pipeline import run_pipeline, write_pipeline_result

_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _process_one(
    input_path: Path,
    output_path: Path | None,
    config: Config,
    check: bool,
    width_mm: float | None = None,
    height_mm: float | None = None,
    batch_rows: list[dict] | None = None,
) -> int:
    """Run the pipeline on one file, print a summary, and return its exit code
    (0 ok, 1 warning, 2 critical) -- 3 (system error) is handled by the caller.

    When `batch_rows` is given (directory mode), a row is appended to it so
    the caller can write a consolidated BAO-CAO.csv after processing every
    file in the directory."""
    is_raster = input_path.suffix.lower() in _RASTER_SUFFIXES
    preview_path = str(input_path.with_name(f"{input_path.stem}_KIEMTRA.png")) if is_raster else None

    result = run_pipeline(
        str(input_path), config, width_mm=width_mm, height_mm=height_mm, preview_path=preview_path
    )
    report = result.report

    click.echo(f"{input_path}: {report.level}")
    for line in report.critical:
        click.secho(f"  CRITICAL: {line}", fg="red")
    for line in report.warnings:
        click.secho(f"  WARNING: {line}", fg="yellow")
    for key, value in sorted(report.info.items()):
        click.echo(f"  {key}: {value}")

    if batch_rows is not None:
        batch_rows.append({"file": input_path.name, "status": report.level, **report.info})

    if not check and report.level != "critical":
        target = output_path if output_path is not None else input_path.with_name(
            f"{input_path.stem}.clean.dxf"
        )
        write_pipeline_result(result, str(target), config)
        click.echo(f"  -> wrote {target}")

    return {"ok": 0, "warning": 1, "critical": 2}[report.level]


def _write_batch_report(batch_rows: list[dict], out_dir: Path) -> Path:
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


def _apply_overrides(config: Config, snap_tol: float | None, weld_mode: str | None, no_simplify: bool) -> Config:
    data = config.model_dump()
    if snap_tol is not None:
        data["snap"]["tolerance"] = snap_tol
    if weld_mode is not None:
        data["weld"]["mode"] = weld_mode
    if no_simplify:
        data["simplify"]["enabled"] = False
    return Config(**data)


@click.command()
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path), default=None,
              help="Output file (single-file mode) or output directory (directory mode).")
@click.option("--config", "config_path", type=click.Path(exists=True, path_type=Path), default=None,
              help="Path to a config.yaml overriding defaults.")
@click.option("--check", is_flag=True, default=False,
              help="Only report; never write an output DXF. Note: for raster input, the "
                   "<stem>_KIEMTRA.png preview image is still written even in check mode, "
                   "and in directory mode BAO-CAO.csv is still written too.")
@click.option("--snap-tol", type=float, default=None, help="Override snap.tolerance.")
@click.option("--weld-mode", type=click.Choice(["off", "overlapping", "all"]), default=None,
              help="Override weld.mode.")
@click.option("--no-simplify", is_flag=True, default=False, help="Disable the simplify stage.")
@click.option("-w", "--width-mm", type=float, default=None,
              help="Target width in mm (raster input only).")
@click.option("-H", "--height-mm", type=float, default=None,
              help="Target height in mm (raster input only, mutually exclusive with --width-mm).")
@click.option("--px-per-mm", type=float, default=None,
              help="Override raster.pixels_per_mm (raster input only).")
@click.option("--invert", is_flag=True, default=False, help="Invert black/white (raster input only).")
@click.option("--raster-threshold", type=int, default=None,
              help="Override raster.threshold, 0-255 (raster input only).")
@click.option("-t", "--thickness", type=float, default=None,
              help="Override validate.material_thickness (sheet metal thickness, mm).")
def main(input_path: Path, output_path: Path | None, config_path: Path | None, check: bool,
         snap_tol: float | None, weld_mode: str | None, no_simplify: bool,
         width_mm: float | None, height_mm: float | None, px_per_mm: float | None,
         invert: bool, raster_threshold: int | None, thickness: float | None) -> None:
    """Clean a DXF file, or a PNG/JPG raster image, (or a directory of either) for laser cutting."""
    if width_mm is not None and height_mm is not None:
        raise click.UsageError("--width-mm and --height-mm are mutually exclusive")

    config = load_config(str(config_path) if config_path else None)
    config = _apply_overrides(config, snap_tol, weld_mode, no_simplify)

    raster_data = config.raster.model_dump()
    if px_per_mm is not None:
        raster_data["pixels_per_mm"] = px_per_mm
    if invert:
        raster_data["invert"] = True
    if raster_threshold is not None:
        raster_data["threshold"] = raster_threshold
    config = config.model_copy(update={"raster": type(config.raster)(**raster_data)})

    if thickness is not None:
        validate_data = config.validate.model_dump()
        validate_data["material_thickness"] = thickness
        config = config.model_copy(update={"validate": type(config.validate)(**validate_data)})

    try:
        if input_path.is_dir():
            if output_path is not None:
                output_path.mkdir(parents=True, exist_ok=True)
            dxf_files = sorted(
                f for f in input_path.iterdir()
                if f.suffix.lower() in {".dxf"} | _RASTER_SUFFIXES
            )
            if not dxf_files:
                click.echo(f"No .dxf files found in {input_path}")
                raise SystemExit(0)
            worst = 0
            batch_rows: list[dict] = []
            for f in dxf_files:
                target = (output_path / f"{f.stem}.clean.dxf") if output_path is not None else None
                try:
                    worst = max(worst, _process_one(f, target, config, check, width_mm, height_mm, batch_rows))
                except Exception as exc:
                    # One broken file must not discard the batch report for
                    # every file that already succeeded -- record it as an
                    # error row and keep processing the rest of the directory.
                    click.secho(f"{f}: System error: {exc}", fg="red", err=True)
                    batch_rows.append({"file": f.name, "status": "error"})
                    worst = max(worst, 3)
            report_dir = output_path if output_path is not None else input_path
            report_path = _write_batch_report(batch_rows, report_dir)
            click.echo(f"Batch report: {report_path}")
            raise SystemExit(worst)
        else:
            code = _process_one(input_path, output_path, config, check, width_mm, height_mm)
            raise SystemExit(code)
    except SystemExit:
        raise
    except Exception as exc:
        click.secho(f"System error: {exc}", fg="red", err=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
