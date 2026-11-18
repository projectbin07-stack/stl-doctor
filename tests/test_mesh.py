from __future__ import annotations

from conftest import load
from stl_doctor.mesh import Mesh, edge_map, weld
from stl_doctor.vec import Triangle


def test_weld_merges_within_tolerance_only() -> None:
    pts = [(0.0, 0.0, 0.0), (0.00005, 0.0, 0.0), (0.0002, 0.0, 0.0), (0.0, 0.0, 0.0)]
    unique, remap = weld(pts, 1e-4)
    assert remap == [0, 0, 1, 0]
    assert unique == [(0.0, 0.0, 0.0), (0.0002, 0.0, 0.0)]


def test_weld_across_hash_cell_boundary() -> None:
    # 0.99999e-4 and 1.00001e-4 fall in different cells but are 2e-9 apart.
    unique, remap = weld([(0.99999e-4, 0.0, 0.0), (1.00001e-4, 0.0, 0.0)], 1e-4)
    assert remap == [0, 0]
    assert len(unique) == 1


def test_exact_weld() -> None:
    unique, remap = weld([(1.0, 2.0, 3.0), (1.0, 2.0, 3.0000001), (1.0, 2.0, 3.0)], 0.0)
    assert remap == [0, 1, 0]
    assert len(unique) == 2


def test_cube_topology_counts() -> None:
    mesh = load("cube_binary.stl")
    assert len(mesh.vertices) == 8
    assert mesh.exact_unique_vertices == 8
    edges = edge_map(mesh)
    # Closed triangulated cube: E = 3F/2 = 18, every edge used by two faces in opposite directions.
    assert len(edges) == 18
    assert all(len(u) == 2 and u[0][1] != u[1][1] for u in edges.values())
    assert mesh.bounds() == ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    assert mesh.face_area(0) == 50.0
    assert mesh.face_normal(0) == (0.0, 0.0, -1.0)


def test_collapsed_faces_are_skipped_in_edge_map() -> None:
    tris: list[Triangle] = [
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ]
    mesh = Mesh.from_triangles(tris)
    assert mesh.is_collapsed(1)
    assert not mesh.is_collapsed(0)
    assert len(edge_map(mesh)) == 3
    assert len(edge_map(mesh, skip={0})) == 0


def test_empty_bounds() -> None:
    mesh = Mesh([], [], [])
    assert mesh.bounds() == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
