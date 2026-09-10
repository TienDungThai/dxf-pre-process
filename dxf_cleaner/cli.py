from pathlib import Path

import click

from dxf_cleaner.config import Config, load_config
from dxf_cleaner.pipeline import run_pipeline, write_pipeline_result


def _process_one(input_path: Path, output_path: Path | None, config: Config, check: bool) -> int:
    """Run the pipeline on one file, print a summary, and return its exit code
    (0 ok, 1 warning, 2 critical) -- 3 (system error) is handled by the caller."""
    result = run_pipeline(str(input_path), config)
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
def main(input_path: Path, output_path: Path | None, config_path: Path | None, check: bool,
         snap_tol: float | None, weld_mode: str | None, no_simplify: bool) -> None:
    """Clean a DXF file (or a directory of DXF files) for laser cutting."""
    config = load_config(str(config_path) if config_path else None)
    config = _apply_overrides(config, snap_tol, weld_mode, no_simplify)

    try:
        if input_path.is_dir():
            if output_path is not None:
                output_path.mkdir(parents=True, exist_ok=True)
            dxf_files = sorted(input_path.glob("*.dxf"))
            if not dxf_files:
                click.echo(f"No .dxf files found in {input_path}")
                raise SystemExit(0)
            worst = 0
            for f in dxf_files:
                target = (output_path / f"{f.stem}.clean.dxf") if output_path is not None else None
                worst = max(worst, _process_one(f, target, config, check))
            raise SystemExit(worst)
        else:
            code = _process_one(input_path, output_path, config, check)
            raise SystemExit(code)
    except SystemExit:
        raise
    except Exception as exc:
        click.secho(f"System error: {exc}", fg="red", err=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
