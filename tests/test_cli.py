import ezdxf
from click.testing import CliRunner

from dxf_cleaner.cli import main


def _clean_square_doc():
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    return doc


def test_single_file_clean_input_exits_zero_and_writes_output(tmp_path):
    doc = _clean_square_doc()
    input_path = tmp_path / "square.dxf"
    doc.saveas(input_path)
    output_path = tmp_path / "out.dxf"

    result = CliRunner().invoke(main, [str(input_path), "-o", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()
    assert "ok" in result.output


def test_check_mode_does_not_write_output(tmp_path):
    doc = _clean_square_doc()
    input_path = tmp_path / "square.dxf"
    doc.saveas(input_path)
    output_path = tmp_path / "out.dxf"

    result = CliRunner().invoke(main, [str(input_path), "-o", str(output_path), "--check"])

    assert result.exit_code == 0
    assert not output_path.exists()


def test_default_output_path_is_input_stem_dot_clean(tmp_path):
    doc = _clean_square_doc()
    input_path = tmp_path / "square.dxf"
    doc.saveas(input_path)

    result = CliRunner().invoke(main, [str(input_path)])

    assert result.exit_code == 0
    assert (tmp_path / "square.clean.dxf").exists()


def test_directory_mode_processes_every_dxf_file(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"
    for name in ("a", "b"):
        _clean_square_doc().saveas(input_dir / f"{name}.dxf")

    result = CliRunner().invoke(main, [str(input_dir), "-o", str(output_dir)])

    assert result.exit_code == 0
    assert (output_dir / "a.clean.dxf").exists()
    assert (output_dir / "b.clean.dxf").exists()


def test_empty_file_produces_critical_exit_code_and_no_output(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    input_path = tmp_path / "empty.dxf"
    doc.saveas(input_path)
    output_path = tmp_path / "out.dxf"

    result = CliRunner().invoke(main, [str(input_path), "-o", str(output_path)])

    assert result.exit_code == 2
    assert not output_path.exists()
    assert "critical" in result.output


def test_weld_mode_override_is_applied(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    # diagonally offset (not edge-aligned) so no edge is collinear with another --
    # an edge-aligned overlap would also trip dedupe's collinear-segment merge,
    # which is a separate concern from what this test is checking.
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 15), (5, 15)], close=True)
    input_path = tmp_path / "overlap.dxf"
    doc.saveas(input_path)

    off_result = CliRunner().invoke(main, [str(input_path), "--check", "--weld-mode", "off"])
    on_result = CliRunner().invoke(main, [str(input_path), "--check", "--weld-mode", "overlapping"])

    # with weld off the two genuinely-overlapping parts trip validate's
    # overlap warning (exit 1); welding them into one part clears it (exit 0).
    assert off_result.exit_code == 1
    assert on_result.exit_code == 0
    assert "weld_count: 0" in off_result.output
    assert "weld_count: 1" in on_result.output


def test_nonexistent_input_path_is_a_usage_error(tmp_path):
    result = CliRunner().invoke(main, [str(tmp_path / "does_not_exist.dxf")])

    assert result.exit_code != 0
