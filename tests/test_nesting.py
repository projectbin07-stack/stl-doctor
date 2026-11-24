"""Nested shells: cavities, islands inside cavities, buried bodies and inside-out files.

Volumes are hand-derived: a 20 mm cube is 8000 mm^3, a 10 mm cube 1000 mm^3, a 4 mm cube 64 mm^3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import make_fixtures
from stl_doctor.analysis import Options, analyze
from stl_doctor.cli import main
from stl_doctor.mesh import Mesh, edge_map
from stl_doctor.repair import repair
from stl_doctor.stlio import write_stl
from stl_doctor.topology import nesting, shells, winding_number
from stl_doctor.vec import Triangle, Vec3


def _box(lo: Vec3, hi: Vec3, *, inward: bool = False) -> list[Triangle]:
    tris = list(make_fixtures.box(lo, hi))
    return [(a, c, b) for a, b, c in tris] if inward else tris


OUTER = ((0.0, 0.0, 0.0), (20.0, 20.0, 20.0))
CAVITY = ((5.0, 5.0, 5.0), (15.0, 15.0, 15.0))
ISLAND = ((8.0, 8.0, 8.0), (12.0, 12.0, 12.0))


def _mesh(*parts: list[Triangle]) -> Mesh:
    return Mesh.from_triangles([t for p in parts for t in p])


def _quick(_mesh: Mesh) -> Options:
    return Options(check_thin_walls=False, suggest_orientation=False)


def _roles(mesh: Mesh) -> list[str]:
    shell_list = shells(mesh, edge_map(mesh), set())
    shell_list.sort(key=lambda s: -abs(s.signed_volume))
    return nesting(mesh, shell_list).role


def test_winding_number_inside_and_outside() -> None:
    mesh = _mesh(_box(*OUTER))
    faces = list(range(len(mesh.faces)))
    assert winding_number(mesh, faces, (10.0, 10.0, 10.0)) == pytest.approx(1.0)
    assert winding_number(mesh, faces, (30.0, 10.0, 10.0)) == pytest.approx(0.0, abs=1e-12)
    inv = _mesh(_box(*OUTER, inward=True))
    assert winding_number(inv, faces, (10.0, 10.0, 10.0)) == pytest.approx(-1.0)


def test_correct_void_is_not_inside_out() -> None:
    mesh = _mesh(_box(*OUTER), _box(*CAVITY, inward=True))
    r = analyze(mesh, _quick(mesh))
    assert r.finding("inside_out") is None
    voids = r.finding("internal_voids")
    assert voids is not None and voids.count == 1
    assert r.stats["volume_mm3"] == pytest.approx(7000.0)  # 8000 - 1000
    assert r.stats["voids"] == 1
    assert r.stats["volume_is_sum_of_overlapping_shells"] is False
    assert r.shell_roles == ["body", "void"]
    assert r.shell_parents == [None, 0]
    shells_json = r.to_dict()["shells"]
    assert [s["role"] for s in shells_json] == ["body", "void"]
    assert r.verdict == "warn"  # only the overhang of the cavity ceiling


def test_void_ceiling_is_the_overhang_not_its_floor() -> None:
    mesh = _mesh(_box(*OUTER), _box(*CAVITY, inward=True))
    r = analyze(mesh, _quick(mesh))
    over = r.finding("overhangs")
    assert over is not None
    # The cavity ceiling (z = 15, 100 mm^2) faces down into the cavity; its floor faces up.
    assert {loc["z"] for loc in over.locations} == {15.0}
    assert r.overhang.area == pytest.approx(100.0)


def test_repair_keeps_a_correct_void() -> None:
    mesh = _mesh(_box(*OUTER), _box(*CAVITY, inward=True))
    fixed, log = repair(mesh)
    assert log.shells_inverted == 0
    after = analyze(fixed, _quick(fixed))
    assert after.stats["volume_mm3"] == pytest.approx(7000.0)
    assert after.finding("inside_out") is None


def test_fully_reversed_hollow_part_is_inside_out() -> None:
    # Both shells reversed: outer has inward normals, the cavity outward ones.
    mesh = _mesh(_box(*OUTER, inward=True), _box(*CAVITY))
    r = analyze(mesh, _quick(mesh))
    inv = r.finding("inside_out")
    assert inv is not None and inv.count == 2
    fixed, log = repair(mesh)
    assert log.shells_inverted == 2
    assert analyze(fixed, _quick(fixed)).stats["volume_mm3"] == pytest.approx(7000.0)


def test_outer_shell_alone_inverted() -> None:
    mesh = _mesh(_box(*OUTER, inward=True), _box(*CAVITY, inward=True))
    r = analyze(mesh, _quick(mesh))
    inv = r.finding("inside_out")
    assert inv is not None and inv.count == 1
    assert inv.locations[0]["volume_mm3"] == pytest.approx(-8000.0)
    fixed, log = repair(mesh)
    assert log.shells_inverted == 1
    assert analyze(fixed, _quick(fixed)).stats["volume_mm3"] == pytest.approx(7000.0)


def test_island_inside_a_void() -> None:
    mesh = _mesh(_box(*OUTER), _box(*CAVITY, inward=True), _box(*ISLAND))
    assert _roles(mesh) == ["body", "void", "body"]
    r = analyze(mesh, _quick(mesh))
    assert r.finding("inside_out") is None
    assert r.stats["volume_mm3"] == pytest.approx(7064.0)  # 8000 - 1000 + 64
    _, log = repair(mesh)
    assert log.shells_inverted == 0


def test_island_inside_a_void_wound_inward_is_inside_out() -> None:
    mesh = _mesh(_box(*OUTER), _box(*CAVITY, inward=True), _box(*ISLAND, inward=True))
    assert _roles(mesh) == ["body", "void", "inside_out"]


def test_body_buried_in_a_body_is_reported_not_flipped() -> None:
    mesh = _mesh(_box(*OUTER), _box(*CAVITY))
    r = analyze(mesh, _quick(mesh))
    assert r.finding("inside_out") is None
    emb = r.finding("embedded_shells")
    assert emb is not None and emb.count == 1
    assert r.stats["volume_mm3"] == pytest.approx(9000.0)
    assert r.stats["volume_is_sum_of_overlapping_shells"] is True
    _, log = repair(mesh)
    assert log.shells_inverted == 0


def test_separate_inverted_part_next_to_a_good_one() -> None:
    mesh = _mesh(_box(*OUTER), _box((30.0, 0.0, 0.0), (40.0, 10.0, 10.0), inward=True))
    assert _roles(mesh) == ["body", "inside_out"]


def test_overlapping_shells_volume_is_labelled_as_a_sum() -> None:
    mesh = _mesh(_box(*OUTER), _box((10.0, 0.0, 0.0), (30.0, 20.0, 20.0)))
    r = analyze(mesh, _quick(mesh))
    # Two 8000 mm^3 cubes overlapping by 4000 mm^3: the sum (16000) is not the union (12000).
    assert r.stats["volume_mm3"] == pytest.approx(16000.0)
    assert r.stats["volume_is_sum_of_overlapping_shells"] is True
    single = _mesh(_box(*OUTER))
    assert analyze(single, _quick(single)).stats["volume_is_sum_of_overlapping_shells"] is False


def test_cli_check_and_repair_of_a_hollow_part(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "hollow.stl"
    write_stl(src, _box(*OUTER) + _box(*CAVITY, inward=True))
    assert main(["check", str(src), "--no-thin-walls"]) == 0
    text = capsys.readouterr().out
    assert "inside_out" not in text
    assert "Internal voids [internal_voids]: 1" in text
    assert "(1 body, 1 void)" in text
    out = tmp_path / "fixed.stl"
    assert main(["repair", str(src), str(out)]) == 0
    text = capsys.readouterr().out
    assert "inverted 0 inside-out shells" in text
    assert "volume 7000.00 -> 7000.00 mm^3" in text


def test_cli_labels_summed_volume(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "overlap.stl"
    write_stl(src, _box(*OUTER) + _box((10.0, 0.0, 0.0), (30.0, 20.0, 20.0)))
    main(["check", str(src), "--no-thin-walls"])
    assert "16000.00 mm^3  (sum of overlapping shells" in capsys.readouterr().out
