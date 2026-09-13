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
) -> int:
    """Run the pipeline on one file, print a summary, and return its exit code
    (0 ok, 1 warning, 2 critical) -- 3 (system error) is handled by the caller."""
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

    if not check and report.level != "critical":
        target = output_path if output_path is not None else input_path.with_name(
            f"{input_path.stem}.clean.dxf"
        )
        write_pipeline_result(result, str(target), config)
        click.echo(f"  -> wrote {target}")

    return {"ok": 0, "warning": 1, "critical": 2}[report.level]


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
@click.option("--check", is_flag=True, default=False, help="Only report; never write an output file.")
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
def main(input_path: Path, output_path: Path | None, config_path: Path | None, check: bool,
         snap_tol: float | None, weld_mode: str | None, no_simplify: bool,
         width_mm: float | None, height_mm: float | None, px_per_mm: float | None,
         invert: bool, raster_threshold: int | None) -> None:
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
            for f in dxf_files:
                target = (output_path / f"{f.stem}.clean.dxf") if output_path is not None else None
                worst = max(worst, _process_one(f, target, config, check, width_mm, height_mm))
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
