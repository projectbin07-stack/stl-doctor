from __future__ import annotations

import math
from pathlib import Path

import pytest

import make_fixtures
from conftest import load
from stl_doctor.analysis import analyze
from stl_doctor.mesh import Mesh, load_mesh
from stl_doctor.repair import ear_clip, fill_loop, repair
from stl_doctor.stlio import write_stl
from stl_doctor.vec import Triangle, Vec3


def _round_trip(mesh: Mesh, tmp_path: Path) -> Mesh:
    out = tmp_path / "out.stl"
    write_stl(out, mesh.triangles())
    return load_mesh(out)


def test_missing_face_cube_becomes_watertight(fmt: str, tmp_path: Path) -> None:
    fixed, log = repair(load(f"cube_missing_face_{fmt}.stl"))
    assert log.holes_filled == 1
    assert log.triangles_added == 2
    assert log.output_faces == 12
    reread = _round_trip(fixed, tmp_path)
    report = analyze(reread)
    assert report.verdict == "pass"
    assert report.stats["watertight"] is True
    assert report.stats["volume_mm3"] == pytest.approx(1000.0, abs=1e-3)


def test_flipped_face_cube_is_fixed(fmt: str, tmp_path: Path) -> None:
    fixed, log = repair(load(f"cube_flipped_face_{fmt}.stl"))
    assert log.faces_flipped == 1
    assert log.holes_filled == 0
    report = analyze(_round_trip(fixed, tmp_path))
    assert report.verdict == "pass"
    assert report.stats["volume_mm3"] == pytest.approx(1000.0, abs=1e-3)


def test_nan_cube_holes_filled(tmp_path: Path) -> None:
    fixed, log = repair(load("nan_vertex_binary.stl"))
    assert log.holes_filled == 2
    assert log.triangles_added == 2
    report = analyze(_round_trip(fixed, tmp_path))
    assert report.stats["volume_mm3"] == pytest.approx(1000.0, abs=1e-3)
    assert report.verdict == "pass"


def test_degenerate_duplicate_and_inside_out(tmp_path: Path) -> None:
    cube = [(a, c, b) for a, b, c in make_fixtures.box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))]
    collapsed: Triangle = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    sliver: Triangle = ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (10.0, 0.0, 0.0))  # collinear
    tris = [*cube, collapsed, cube[3], sliver]
    fixed, log = repair(Mesh.from_triangles(tris))
    assert log.degenerate_removed == 2
    assert log.duplicates_removed == 1
    assert log.shells_inverted == 1
    assert log.output_faces == 12
    report = analyze(_round_trip(fixed, tmp_path))
    assert report.verdict == "pass"
    assert report.stats["volume_mm3"] == pytest.approx(1000.0, abs=1e-3)
    assert report.stats["vertices"] == 8  # the sliver's midpoint vertex was dropped


def test_near_duplicate_weld_counted() -> None:
    cube = make_fixtures.box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    a, b, c = cube[0]
    cube[0] = ((a[0] + 5e-5, a[1], a[2]), b, c)
    _, log = repair(Mesh.from_triangles(cube))
    assert log.near_duplicate_vertices_merged == 1


def test_no_fill_option() -> None:
    fixed, log = repair(load("cube_missing_face_binary.stl"), fill_holes=False)
    assert log.holes_filled == 0
    assert len(fixed.faces) == 10


def test_nonplanar_hole_is_skipped() -> None:
    # Open box without its top, with one top corner raised by 3 mm: the rim is not planar.
    cube = make_fixtures.box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    open_box = cube[:2] + cube[4:]
    raised = [
        tuple((x, y, 13.0) if (x, y, z) == (10.0, 10.0, 10.0) else (x, y, z) for x, y, z in t)
        for t in open_box
    ]
    fixed, log = repair(Mesh.from_triangles(raised))  # type: ignore[arg-type]
    assert log.holes_filled == 0
    assert len(log.holes_skipped) == 1
    assert "not planar" in log.holes_skipped[0]
    assert len(fixed.faces) == 10


def test_ear_clip_concave_l_shape() -> None:
    # L-shaped hexagon, counter-clockwise; area 3 (unit squares).
    poly = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0)]
    tris = ear_clip(poly)
    assert tris is not None
    assert len(tris) == 4
    total = 0.0
    for i, j, k in tris:
        (ax, ay), (bx, by), (cx, cy) = poly[i], poly[j], poly[k]
        signed = ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / 2
        assert signed > 0  # every ear keeps the polygon's orientation
        total += signed
    assert total == pytest.approx(3.0)


def test_ear_clip_rejects_clockwise_or_degenerate() -> None:
    assert ear_clip([(0.0, 0.0), (0.0, 1.0), (1.0, 0.0)]) is None
    assert ear_clip([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]) is None


def test_fill_loop_rejects_bad_loops() -> None:
    verts: list[Vec3] = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    assert fill_loop(verts, [0, 1], 0.01)[0] is None
    assert "touches itself" in fill_loop(verts, [0, 1, 3, 1], 0.01)[1]
    assert "zero area" in fill_loop(verts, [0, 1, 2], 0.01)[1]
    faces, reason = fill_loop(verts, [0, 1, 3], 0.01)
    assert reason == ""
    # The patch walks the loop backwards so it continues the surrounding winding.
    assert faces == [(3, 1, 0)]


def test_repaired_mesh_is_a_new_object() -> None:
    mesh = load("cube_flipped_face_binary.stl")
    before = list(mesh.faces)
    repair(mesh)
    assert mesh.faces == before
    assert not math.isnan(mesh.vertices[0][0])
