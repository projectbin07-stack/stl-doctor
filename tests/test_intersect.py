from __future__ import annotations

import pytest

from stl_doctor.intersect import self_intersections, triangles_intersect
from stl_doctor.mesh import Mesh
from stl_doctor.vec import Triangle

FLOOR: Triangle = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (0.0, 4.0, 0.0))


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        # Pierces the floor triangle through its interior.
        (((1.0, 1.0, -1.0), (1.0, 1.0, 1.0), (2.0, 1.0, 1.0)), True),
        # Above the floor: separated by the plane test.
        (((1.0, 1.0, 1.0), (2.0, 1.0, 1.0), (1.0, 2.0, 2.0)), False),
        # Crosses the floor plane but outside the triangle (x + y > 4).
        (((3.0, 3.0, -1.0), (3.0, 3.0, 1.0), (4.0, 3.0, 1.0)), False),
        # Touches the floor at a single point only: not reported.
        (((1.0, 1.0, 0.0), (1.0, 1.0, 1.0), (2.0, 1.0, 1.0)), False),
        # Coplanar and overlapping.
        (((1.0, 1.0, 0.0), (5.0, 1.0, 0.0), (1.0, 5.0, 0.0)), True),
        # Coplanar, one strictly inside the other (no edge crossings).
        (((0.5, 0.5, 0.0), (1.0, 0.5, 0.0), (0.5, 1.0, 0.0)), True),
        # Coplanar and disjoint.
        (((5.0, 5.0, 0.0), (6.0, 5.0, 0.0), (5.0, 6.0, 0.0)), False),
        # Parallel planes.
        (((0.0, 0.0, 0.5), (4.0, 0.0, 0.5), (0.0, 4.0, 0.5)), False),
        # Degenerate partner.
        (((1.0, 1.0, -1.0), (1.0, 1.0, 0.0), (1.0, 1.0, 1.0)), False),
    ],
)
def test_triangle_pairs(other: Triangle, expected: bool) -> None:
    assert triangles_intersect(FLOOR, other) is expected
    assert triangles_intersect(other, FLOOR) is expected


def test_shared_vertex_pairs_are_skipped() -> None:
    tris: list[Triangle] = [FLOOR, ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1.0, 1.0, -1.0))]
    mesh = Mesh.from_triangles(tris)
    assert triangles_intersect(tris[0], tris[1]) is True
    assert self_intersections(mesh, [0, 1]) == []


def test_grid_finds_pairs_among_many() -> None:
    tris: list[Triangle] = []
    # A row of 20 separate small floor triangles and one blade cutting through the 5th.
    for i in range(20):
        x = i * 3.0
        tris.append(((x, 0.0, 0.0), (x + 2.0, 0.0, 0.0), (x, 2.0, 0.0)))
    tris.append(((12.5, 0.5, -1.0), (12.5, 0.5, 1.0), (13.0, 0.5, 1.0)))
    mesh = Mesh.from_triangles(tris)
    assert self_intersections(mesh, list(range(len(tris)))) == [(4, 20)]
