from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import make_fixtures
from conftest import FIXTURES
from stl_doctor import __version__
from stl_doctor.cli import main
from stl_doctor.mesh import load_mesh
from stl_doctor.stlio import write_stl


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def fx(name: str) -> str:
    return str(FIXTURES / name)


def test_check_pass_exit_zero() -> None:
    code, out, _ = run("check", fx("cube_binary.stl"))
    assert code == 0
    assert "verdict: PASS (0 errors, 0 warnings, 0 notes)" in out
    assert "10.00 x 10.00 x 10.00 mm, fits Bambu Lab P1S (256 x 256 x 256 mm)" in out
    assert "volume     1000.00 mm^3" in out


def test_check_errors_exit_two() -> None:
    code, out, _ = run("check", fx("cube_missing_face_binary.stl"))
    assert code == 2
    assert "FAIL  Holes (open boundaries) [open_boundaries]: 1" in out
    assert "(surface not closed and consistent: unreliable)" in out


def test_strict_turns_warnings_into_exit_one() -> None:
    assert run("check", fx("overhang_t_binary.stl"))[0] == 0
    code, out, _ = run("check", fx("overhang_t_binary.stl"), "--strict")
    assert code == 1
    assert "rotate 90.0 deg about X, then 0.0 deg about Y -> overhang 200.00 -> 0.00 mm^2" in out


def test_json_output() -> None:
    code, out, _ = run("check", fx("thin_plate_ascii.stl"), "--json", "--nozzle", "0.6")
    assert code == 0
    data = json.loads(out)
    assert data["verdict"] == "warn"
    assert data["options"]["thin_wall_threshold_mm"] == pytest.approx(1.2)
    thin = next(f for f in data["findings"] if f["code"] == "thin_walls")
    assert thin["count"] == 4
    assert thin["locations"][0]["thickness_mm"] == 0.6


def test_printer_and_custom_bed(tmp_path: Path) -> None:
    big = tmp_path / "big.stl"
    write_stl(big, make_fixtures.box((0.0, 0.0, 0.0), (200.0, 50.0, 50.0)))
    assert run("check", str(big))[0] == 0
    code, out, _ = run("check", str(big), "--printer", "a1-mini")
    assert code == 2
    assert "does not fit on Bambu Lab A1 mini (180 x 180 x 180 mm)" in out
    assert run("check", str(big), "--bed", "220x220x250")[0] == 0
    # Registry names and aliases work as well as keys.
    code, out, _ = run("check", str(big), "--printer", "A1 mini")
    assert code == 2
    assert "Bambu Lab A1 mini" in out
    assert "Bambu Lab X1 Carbon" in run("check", str(big), "--printer", "X1C")[1]


def test_html_report_written(tmp_path: Path) -> None:
    report = tmp_path / "r.html"
    code, out, _ = run("check", fx("overhang_t_ascii.stl"), "--report", str(report))
    assert code == 0
    assert f"report written to {report}" in out
    assert report.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_flags_skip_checks() -> None:
    code, out, _ = run(
        "check", fx("overlapping_cubes_binary.stl"), "--no-intersections", "--no-thin-walls"
    )
    assert code == 0
    assert "self_intersections" not in out
    assert "no sampled orientation resting on a face has less overhang" in out


def test_unreadable_files_exit_two() -> None:
    code, _, err = run("check", fx("corrupt_truncated_binary.stl"))
    assert code == 2
    assert "looks truncated" in err
    code, _, err = run("check", fx("does_not_exist.stl"))
    assert code == 2
    assert "cannot read" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["check", "x.stl", "--nozzle", "0"],
        ["check", "x.stl", "--nozzle", "abc"],
        ["check", "x.stl", "--overhang", "90"],
        ["check", "x.stl", "--weld-tol", "-1"],
        ["check", "x.stl", "--weld-tol", "q"],
        ["check", "x.stl", "--bed", "100x100"],
        ["check", "x.stl", "--printer", "ender3"],
    ],
)
def test_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_repair_round_trip(tmp_path: Path) -> None:
    out_file = tmp_path / "fixed.stl"
    code, out, _ = run("repair", fx("cube_missing_face_ascii.stl"), str(out_file))
    assert code == 0
    assert "filled 1 hole with 2 triangles" in out
    assert "watertight: False -> True, volume 666.67 -> 1000.00 mm^3" in out
    assert run("check", str(out_file))[0] == 0
    # Refuses to overwrite without --force; --force with --ascii and --json works.
    code, _, err = run("repair", fx("cube_missing_face_ascii.stl"), str(out_file))
    assert code == 2
    assert "pass --force" in err
    code, out, _ = run(
        "repair", fx("cube_flipped_face_binary.stl"), str(out_file), "--force", "--ascii", "--json"
    )
    assert code == 0
    data = json.loads(out)
    assert data["changes"]["faces_flipped"] == 1
    assert data["before"]["verdict"] == "fail"
    assert data["after"]["verdict"] == "pass"
    assert out_file.read_bytes().startswith(b"solid")


def test_repair_never_overwrites_input(tmp_path: Path) -> None:
    src = tmp_path / "in.stl"
    src.write_bytes((FIXTURES / "cube_missing_face_binary.stl").read_bytes())
    code, _, err = run("repair", str(src), str(src), "--force")
    assert code == 2
    assert "refusing to overwrite the input" in err
    assert src.read_bytes() == (FIXTURES / "cube_missing_face_binary.stl").read_bytes()


def test_repair_reports_remaining_errors(tmp_path: Path) -> None:
    code, out, _ = run("repair", fx("nonmanifold_edge_binary.stl"), str(tmp_path / "o.stl"))
    assert code == 2
    assert "still FAIL: Non-manifold edges (1)" in out


def test_orient_text_json_and_output(tmp_path: Path) -> None:
    code, out, _ = run("orient", fx("overhang_t_binary.stl"), "--top", "2")
    assert code == 0
    assert "current: overhang 200.00 mm^2, height 25.00 mm" in out
    assert "#1: rotate   90.00 deg about X" in out
    rotated = tmp_path / "rot.stl"
    code, out, _ = run("orient", fx("overhang_t_binary.stl"), "--json", "-o", str(rotated))
    data = json.loads(out)
    assert data["current"]["overhang_mm2"] == 200.0
    assert data["candidates"][0]["overhang_mm2"] == 0.0
    assert len(data["candidates"]) == 3
    mesh = load_mesh(rotated)
    lo, hi = mesh.bounds()
    assert lo[2] == pytest.approx(0.0, abs=1e-5)
    assert hi[2] - lo[2] == pytest.approx(10.0, abs=1e-4)
    code, out, _ = run("check", str(rotated))
    assert code == 0
    assert "overhangs" not in out
    assert run("orient", fx("cube_binary.stl"), "--top", "0")[0] == 2


def test_version_and_module_entry_point() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    proc = subprocess.run(
        [sys.executable, "-m", "stl_doctor", "check", fx("cube_ascii.stl"), "--json"],
        capture_output=True,
        text=True,
        check=False,
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["version"] == __version__
