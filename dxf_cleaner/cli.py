from pathlib import Path

import click

from dxf_cleaner.config import load_config
from dxf_cleaner.core import (
    RASTER_SUFFIXES,
    apply_overrides,
    apply_raster_overrides,
    apply_thickness_override,
    process_path,
)


def _click_log(level: str, message: str) -> None:
    color = {"critical": "red", "warning": "yellow"}.get(level)
    click.secho(message, fg=color) if color else click.echo(message)


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
    config = apply_overrides(config, snap_tol, weld_mode, no_simplify)
    config = apply_raster_overrides(config, px_per_mm, invert, raster_threshold)
    config = apply_thickness_override(config, thickness)

    try:
        worst, report_path = process_path(
            input_path, output_path, config, check, _click_log, width_mm, height_mm
        )
        if report_path is not None:
            click.echo(f"Batch report: {report_path}")
        raise SystemExit(worst)
    except SystemExit:
        raise
    except Exception as exc:
        click.secho(f"System error: {exc}", fg="red", err=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
