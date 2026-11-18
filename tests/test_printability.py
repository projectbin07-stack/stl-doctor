from __future__ import annotations

import math

import pytest

import make_fixtures
from conftest import load
from stl_doctor.mesh import Mesh
from stl_doctor.printability import (
    best_orientations,
    fibonacci_directions,
    numpy_available,
    overhangs,
    thin_walls,
)
from stl_doctor.vec import Triangle, length, rotate_xy, rotation_to_z


def _tilted_face_mesh(tilt_from_vertical_deg: float) -> Mesh:
    """A small downward-facing triangle raised 5 mm above a bed triangle at z = 0.

    Its unit normal is n = (cos(t), 0, -sin(t)) where t is the tilt away from a vertical wall,
    so t = 0 is a wall and t = 90 a flat ceiling. Area is 0.5 mm^2 (unit legs).
    """
    t = math.radians(tilt_from_vertical_deg)
    # Edge vectors: e1 along Y, e2 in the XZ plane perpendicular to n.
    e1 = (0.0, 1.0, 0.0)
    e2 = (math.sin(t), 0.0, math.cos(t))
    p0 = (0.0, 0.0, 5.0)
    p1 = (p0[0] + e2[0], p0[1] + e2[1], p0[2] + e2[2])
    p2 = (p0[0] + e1[0], p0[1] + e1[1], p0[2] + e1[2])
    bed: Triangle = ((10.0, 0.0, 0.0), (11.0, 0.0, 0.0), (10.0, 1.0, 0.0))
    # (p0, p2, p1): normal = e1 x e2 = (cos t, 0, -sin t), pointing down.
    return Mesh.from_triangles([(p0, p2, p1), bed])


@pytest.mark.parametrize(
    ("tilt", "limit", "expected"),
    [(30.0, 45.0, False), (60.0, 45.0, True), (30.0, 20.0, True), (89.0, 45.0, True)],
)
def test_overhang_angle_threshold(tilt: float, limit: float, expected: bool) -> None:
    mesh = _tilted_face_mesh(tilt)
    n = mesh.face_normal(0)
    assert n[2] == pytest.approx(-math.sin(math.radians(tilt)))
    result = overhangs(mesh, [0, 1], limit)
    assert (0 in result.faces) is expected
    if expected:
        assert result.area == pytest.approx(0.5)
        assert result.projected_area == pytest.approx(0.5 * math.sin(math.radians(tilt)))


def test_bed_faces_are_not_overhangs() -> None:
    cube = load("cube_binary.stl")
    assert overhangs(cube, list(range(12)), 45.0).faces == []
    # The bottom faces point straight down; lifting the bed reference would count them.
    lifted = Mesh.from_triangles(
        [*cube.triangles(), ((0.0, 0.0, -5.0), (1.0, 0.0, -5.0), (0.0, 1.0, -5.0))]
    )
    assert sorted(overhangs(lifted, list(range(12)), 45.0).faces) == [0, 1]


def test_thin_walls_threshold() -> None:
    plate = load("thin_plate_binary.stl")
    faces = list(range(len(plate.faces)))
    assert thin_walls(plate, faces, 0.8).faces == [4, 5, 6, 7]
    assert thin_walls(plate, faces, 0.6).faces == []  # strictly thinner than the limit
    # Wider tilt window still ignores the horizontal top and bottom.
    assert thin_walls(plate, faces, 0.8, max_tilt_deg=80.0).faces == [4, 5, 6, 7]


def test_thin_wall_ignores_hits_beyond_threshold() -> None:
    plate = Mesh.from_triangles(make_fixtures.box((0.0, 0.0, 0.0), (20.0, 3.0, 15.0)))
    assert thin_walls(plate, list(range(12)), 0.8).faces == []
    assert thin_walls(plate, list(range(12)), 3.5).faces == [4, 5, 6, 7]


def test_fibonacci_directions_are_unit() -> None:
    dirs = fibonacci_directions(50)
    assert len(dirs) == 56
    assert all(length(d) == pytest.approx(1.0) for d in dirs)


@pytest.mark.parametrize(
    "up", [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.6, 0.0, 0.8)]
)
def test_rotation_maps_up_to_z(up: tuple[float, float, float]) -> None:
    rx, ry = rotation_to_z(up)
    assert rotate_xy(up, rx, ry) == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)


def test_orientation_pure_and_numpy_agree() -> None:
    mesh = load("overhang_t_binary.stl")
    faces = list(range(len(mesh.faces)))
    pure = best_orientations(mesh, faces, 45.0, 60, use_numpy=False)
    assert pure[0].overhang_area == pytest.approx(0.0)
    assert pure[0].contact_area == pytest.approx(350.0)
    assert pure[0].height == pytest.approx(10.0)
    current = next(c for c in pure if c.up == (0.0, 0.0, 1.0))
    assert current.overhang_area == pytest.approx(200.0)
    assert current.contact_area == pytest.approx(100.0)  # stem footprint 10 x 10
    if numpy_available():
        fast = best_orientations(mesh, faces, 45.0, 60, use_numpy=True)
        assert [c.up for c in fast] == [c.up for c in pure]
        for a, b in zip(fast, pure, strict=True):
            assert a.overhang_area == pytest.approx(b.overhang_area)
            assert a.contact_area == pytest.approx(b.contact_area)


def test_orientation_prefers_resting_on_a_face() -> None:
    # Two overlapping boxes have overhang in every axis-aligned pose; corner-down poses have none
    # but no bed contact, so they must rank after poses that rest on a face.
    mesh = load("overlapping_cubes_binary.stl")
    cands = best_orientations(mesh, list(range(len(mesh.faces))), 45.0, 100)
    assert cands[0].contact_area > 0
