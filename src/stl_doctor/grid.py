"""Uniform-grid broad phase over triangles, shared by the intersection and thickness checks.

Each triangle is registered in every cell its axis-aligned bounding box overlaps. The cell size
defaults to the mean triangle extent, clamped so the grid never has more than about ``8 * F`` cells
along the populated region. Queries return the candidate triangles whose cells overlap a box.

Cost: building is O(sum of cells covered); a query is O(cells covered + candidates). For meshes of
similar-sized triangles this is close to linear; a few very large triangles next to many small
ones degrade it towards O(F^2) because the large triangles land in many cells.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

from .mesh import Mesh
from .vec import Vec3, cross, dot, sub

Cell = tuple[int, int, int]


def triangle_box(mesh: Mesh, f: int) -> tuple[Vec3, Vec3]:
    a, b, c = mesh.triangle(f)
    lo = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]), min(a[2], b[2], c[2]))
    hi = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]), max(a[2], b[2], c[2]))
    return lo, hi


def boxes_overlap(alo: Vec3, ahi: Vec3, blo: Vec3, bhi: Vec3, eps: float = 0.0) -> bool:
    return (
        alo[0] <= bhi[0] + eps
        and blo[0] <= ahi[0] + eps
        and alo[1] <= bhi[1] + eps
        and blo[1] <= ahi[1] + eps
        and alo[2] <= bhi[2] + eps
        and blo[2] <= ahi[2] + eps
    )


class TriangleGrid:
    def __init__(self, mesh: Mesh, faces: Iterable[int], cell_size: float | None = None) -> None:
        self.mesh = mesh
        self.faces = list(faces)
        self.boxes: dict[int, tuple[Vec3, Vec3]] = {f: triangle_box(mesh, f) for f in self.faces}
        if cell_size is None:
            cell_size = self._default_cell_size()
        self.cell = cell_size
        self.cells: dict[Cell, list[int]] = defaultdict(list)
        for f in self.faces:
            lo, hi = self.boxes[f]
            for key in self._cells_of(lo, hi):
                self.cells[key].append(f)

    def _default_cell_size(self) -> float:
        if not self.faces:
            return 1.0
        total = 0.0
        glo = [math.inf] * 3
        ghi = [-math.inf] * 3
        for lo, hi in self.boxes.values():
            total += max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
            for i in range(3):
                glo[i] = min(glo[i], lo[i])
                ghi[i] = max(ghi[i], hi[i])
        mean = total / len(self.faces)
        span = max(ghi[i] - glo[i] for i in range(3))
        # Keep the cell count bounded: at most ~(8F)^(1/3) cells along the longest axis.
        floor = span / max(1.0, (8.0 * len(self.faces)) ** (1.0 / 3.0))
        size = max(mean, floor)
        return size if size > 0 else 1.0

    def _key(self, p: Vec3) -> Cell:
        s = self.cell
        return (math.floor(p[0] / s), math.floor(p[1] / s), math.floor(p[2] / s))

    def _cells_of(self, lo: Vec3, hi: Vec3) -> Iterable[Cell]:
        k0 = self._key(lo)
        k1 = self._key(hi)
        for i in range(k0[0], k1[0] + 1):
            for j in range(k0[1], k1[1] + 1):
                for k in range(k0[2], k1[2] + 1):
                    yield (i, j, k)

    def query(self, lo: Vec3, hi: Vec3) -> set[int]:
        found: set[int] = set()
        for key in self._cells_of(lo, hi):
            bucket = self.cells.get(key)
            if bucket:
                found.update(bucket)
        return found

    def candidate_pairs(self) -> set[tuple[int, int]]:
        """Pairs (i < j) of triangles that share a grid cell and whose boxes overlap."""
        pairs: set[tuple[int, int]] = set()
        boxes = self.boxes
        for bucket in self.cells.values():
            n = len(bucket)
            for x in range(n):
                fi = bucket[x]
                lo_i, hi_i = boxes[fi]
                for y in range(x + 1, n):
                    fj = bucket[y]
                    key = (fi, fj) if fi < fj else (fj, fi)
                    if key in pairs:
                        continue
                    lo_j, hi_j = boxes[fj]
                    if boxes_overlap(lo_i, hi_i, lo_j, hi_j):
                        pairs.add(key)
        return pairs


def ray_triangle(origin: Vec3, direction: Vec3, a: Vec3, b: Vec3, c: Vec3) -> float | None:
    """Moeller-Trumbore ray/triangle test; returns the ray parameter ``t`` of the hit or ``None``.

    Hits from either side count. ``direction`` need not be unit length; ``t`` is in its units.
    """
    e1 = sub(b, a)
    e2 = sub(c, a)
    p = cross(direction, e2)
    det = dot(e1, p)
    if abs(det) < 1e-14:
        return None
    inv = 1.0 / det
    s = sub(origin, a)
    u = dot(s, p) * inv
    if u < 0.0 or u > 1.0:
        return None
    q = cross(s, e1)
    v = dot(direction, q) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    return dot(e2, q) * inv
