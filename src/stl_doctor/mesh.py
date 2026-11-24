"""Indexed mesh built from a triangle soup by welding vertices, plus edge topology."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .stlio import SoupObject, STLError, TriangleSoup, read_stl_bytes
from .threemf import read_3mf
from .vec import Triangle, Vec3, centroid, distance, normalize, triangle_area, triangle_cross

Face = tuple[int, int, int]
Edge = tuple[int, int]
# Undirected edge (low, high) -> list of (face index, +1 if the face walks low->high else -1)
EdgeMap = dict[Edge, list[tuple[int, int]]]

DEFAULT_WELD_TOLERANCE = 1e-4  # mm


def weld(points: list[Vec3], tolerance: float) -> tuple[list[Vec3], list[int]]:
    """Merge points closer than ``tolerance`` using a spatial hash.

    Returns the unique points and, for each input point, the index of its representative. A point
    joins the first earlier representative within ``tolerance`` found in the 27 neighbouring hash
    cells (cell size = tolerance), so the result is deterministic for a given input order.
    ``tolerance <= 0`` merges only bit-identical coordinates.
    """
    unique: list[Vec3] = []
    remap: list[int] = []
    if tolerance <= 0:
        exact: dict[Vec3, int] = {}
        for p in points:
            idx = exact.get(p)
            if idx is None:
                idx = exact[p] = len(unique)
                unique.append(p)
            remap.append(idx)
        return unique, remap
    inv = 1.0 / tolerance
    grid: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for p in points:
        cx, cy, cz = math.floor(p[0] * inv), math.floor(p[1] * inv), math.floor(p[2] * inv)
        found = -1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for idx in grid.get((cx + dx, cy + dy, cz + dz), ()):
                        if distance(unique[idx], p) <= tolerance and (found < 0 or idx < found):
                            found = idx
        if found < 0:
            found = len(unique)
            unique.append(p)
            grid[(cx, cy, cz)].append(found)
        remap.append(found)
    return unique, remap


@dataclass
class Mesh:
    vertices: list[Vec3]
    faces: list[Face]
    stored_normals: list[Vec3]
    weld_tolerance: float = DEFAULT_WELD_TOLERANCE
    exact_unique_vertices: int = 0
    source_format: str = ""
    name: str = ""
    source_count: int = 0
    non_finite: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Objects of a multi-object file (3MF build items); faces keep the soup's triangle order.
    objects: list[SoupObject] = field(default_factory=list)

    @classmethod
    def from_soup(cls, soup: TriangleSoup, tolerance: float = DEFAULT_WELD_TOLERANCE) -> Mesh:
        points = [v for tri in soup.triangles for v in tri]
        vertices, remap = weld(points, tolerance)
        faces = [(remap[i], remap[i + 1], remap[i + 2]) for i in range(0, len(remap), 3)]
        return cls(
            vertices=vertices,
            faces=faces,
            stored_normals=list(soup.normals),
            weld_tolerance=tolerance,
            exact_unique_vertices=len(set(points)),
            source_format=soup.fmt,
            name=soup.name,
            source_count=soup.source_count,
            non_finite=list(soup.non_finite),
            warnings=list(soup.warnings),
            objects=list(soup.objects),
        )

    @classmethod
    def from_triangles(
        cls, triangles: list[Triangle], tolerance: float = DEFAULT_WELD_TOLERANCE
    ) -> Mesh:
        soup = TriangleSoup(triangles, [(0.0, 0.0, 0.0)] * len(triangles), "memory")
        soup.source_count = len(triangles)
        return cls.from_soup(soup, tolerance)

    def triangle(self, f: int) -> Triangle:
        a, b, c = self.faces[f]
        return (self.vertices[a], self.vertices[b], self.vertices[c])

    def triangles(self) -> list[Triangle]:
        return [self.triangle(f) for f in range(len(self.faces))]

    def face_cross(self, f: int) -> Vec3:
        return triangle_cross(*self.triangle(f))

    def face_normal(self, f: int) -> Vec3:
        return normalize(self.face_cross(f))

    def face_area(self, f: int) -> float:
        return triangle_area(*self.triangle(f))

    def face_centroid(self, f: int) -> Vec3:
        return centroid(*self.triangle(f))

    def bounds(self) -> tuple[Vec3, Vec3]:
        used = {i for face in self.faces for i in face} if self.faces else set()
        pts = [self.vertices[i] for i in used] or [(0.0, 0.0, 0.0)]
        lo = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts))
        hi = (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
        return lo, hi

    def is_collapsed(self, f: int) -> bool:
        """True when welding made two corners of the face the same vertex."""
        a, b, c = self.faces[f]
        return a in (b, c) or b == c


def edge_map(mesh: Mesh, skip: set[int] | None = None) -> EdgeMap:
    """Map each undirected edge to the faces using it, with the direction each face walks it."""
    edges: EdgeMap = defaultdict(list)
    for f, (a, b, c) in enumerate(mesh.faces):
        if skip and f in skip:
            continue
        if a in (b, c) or b == c:
            continue
        for u, v in ((a, b), (b, c), (c, a)):
            if u < v:
                edges[(u, v)].append((f, 1))
            else:
                edges[(v, u)].append((f, -1))
    return dict(edges)


def load_mesh(path: str | Path, tolerance: float = DEFAULT_WELD_TOLERANCE) -> Mesh:
    """Load STL (ASCII or binary) or 3MF, detected by content, and weld its vertices."""
    p = Path(path)
    try:
        with p.open("rb") as fh:
            magic = fh.read(4)
    except OSError as exc:
        raise STLError(f"cannot read {path}: {exc.strerror or exc}") from exc
    soup = read_3mf(p) if magic == b"PK\x03\x04" else read_stl_bytes(p.read_bytes())
    return Mesh.from_soup(soup, tolerance)
