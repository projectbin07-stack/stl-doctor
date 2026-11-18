"""Self-intersection candidates: triangle/triangle tests behind a uniform-grid broad phase.

Narrow phase (written for this project, following the classic plane-separation idea):

1. If all corners of triangle P lie strictly on one side of Q's plane (or vice versa), they do not
   intersect.
2. If both are coplanar, they intersect when an edge of one properly crosses an edge of the other
   or the centroid of one lies strictly inside the other (2D tests after dropping the dominant
   normal axis).
3. Otherwise each triangle meets the other's plane in a segment lying on the line where the two
   planes meet; the triangles intersect when those two segments overlap by more than ``eps``.

Contacts that touch only at a point or along a zero-length overlap are *not* reported, and pairs
that share a vertex index are skipped, so folds between adjacent triangles are not detected.
Expected cost is near-linear for evenly sized triangles; the worst case is O(F^2) pair tests.
"""

from __future__ import annotations

from .grid import TriangleGrid
from .mesh import Mesh
from .vec import Triangle, Vec3, cross, dot, length, normalize, sub


def _plane_distances(n: Vec3, origin: Vec3, tri: Triangle, eps: float) -> list[float]:
    out = []
    for p in tri:
        d = dot(n, sub(p, origin))
        out.append(0.0 if abs(d) <= eps else d)
    return out


def _segment_on_line(tri: Triangle, dist: list[float], axis: Vec3) -> tuple[float, float] | None:
    ts: list[float] = []
    for i in range(3):
        j = (i + 1) % 3
        di, dj = dist[i], dist[j]
        if di == 0.0:
            ts.append(dot(axis, tri[i]))
        if di * dj < 0.0:
            s = di / (di - dj)
            p = tri[i]
            q = tri[j]
            pt = (p[0] + (q[0] - p[0]) * s, p[1] + (q[1] - p[1]) * s, p[2] + (q[2] - p[2]) * s)
            ts.append(dot(axis, pt))
    if not ts:
        return None
    return min(ts), max(ts)


def _project(tri: Triangle, drop: int) -> list[tuple[float, float]]:
    keep = [i for i in range(3) if i != drop]
    return [(p[keep[0]], p[keep[1]]) for p in tri]


def _orient2d(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(
    p1: tuple[float, float],
    p2: tuple[float, float],
    q1: tuple[float, float],
    q2: tuple[float, float],
    eps: float,
) -> bool:
    d1 = _orient2d(q1, q2, p1)
    d2 = _orient2d(q1, q2, p2)
    d3 = _orient2d(p1, p2, q1)
    d4 = _orient2d(p1, p2, q2)
    return ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and (
        (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)
    )


def _strictly_inside(pt: tuple[float, float], tri: list[tuple[float, float]], eps: float) -> bool:
    d1 = _orient2d(tri[0], tri[1], pt)
    d2 = _orient2d(tri[1], tri[2], pt)
    d3 = _orient2d(tri[2], tri[0], pt)
    return (d1 > eps and d2 > eps and d3 > eps) or (d1 < -eps and d2 < -eps and d3 < -eps)


def _coplanar(p: Triangle, q: Triangle, n: Vec3, eps: float) -> bool:
    drop = max(range(3), key=lambda i: abs(n[i]))
    p2 = _project(p, drop)
    q2 = _project(q, drop)
    area_eps = eps * eps
    for i in range(3):
        for j in range(3):
            if _segments_cross(p2[i], p2[(i + 1) % 3], q2[j], q2[(j + 1) % 3], area_eps):
                return True
    pc = ((p2[0][0] + p2[1][0] + p2[2][0]) / 3, (p2[0][1] + p2[1][1] + p2[2][1]) / 3)
    qc = ((q2[0][0] + q2[1][0] + q2[2][0]) / 3, (q2[0][1] + q2[1][1] + q2[2][1]) / 3)
    return _strictly_inside(pc, q2, area_eps) or _strictly_inside(qc, p2, area_eps)


def triangles_intersect(p: Triangle, q: Triangle, eps: float = 1e-9) -> bool:
    """True when triangles ``p`` and ``q`` overlap by more than ``eps`` (a length in mm)."""
    n1 = normalize(cross(sub(p[1], p[0]), sub(p[2], p[0])))
    n2 = normalize(cross(sub(q[1], q[0]), sub(q[2], q[0])))
    if n1 == (0.0, 0.0, 0.0) or n2 == (0.0, 0.0, 0.0):
        return False  # degenerate triangles are reported separately
    dp = _plane_distances(n2, q[0], p, eps)
    if all(d > 0 for d in dp) or all(d < 0 for d in dp):
        return False
    dq = _plane_distances(n1, p[0], q, eps)
    if all(d > 0 for d in dq) or all(d < 0 for d in dq):
        return False
    if all(d == 0.0 for d in dp):
        return _coplanar(p, q, n1, eps)
    axis = cross(n1, n2)
    if length(axis) < 1e-12:
        return False  # parallel planes, not coplanar within eps
    axis = normalize(axis)
    sp = _segment_on_line(p, dp, axis)
    sq = _segment_on_line(q, dq, axis)
    if sp is None or sq is None:
        return False
    return min(sp[1], sq[1]) - max(sp[0], sq[0]) > eps


def self_intersections(
    mesh: Mesh, faces: list[int], eps: float | None = None
) -> list[tuple[int, int]]:
    """All pairs of faces (from ``faces``) that intersect and share no vertex."""
    if eps is None:
        lo, hi = mesh.bounds()
        eps = max(1e-9, 1e-7 * max(hi[i] - lo[i] for i in range(3)))
    grid = TriangleGrid(mesh, faces)
    hits: list[tuple[int, int]] = []
    for i, j in sorted(grid.candidate_pairs()):
        fi, fj = mesh.faces[i], mesh.faces[j]
        if fi[0] in fj or fi[1] in fj or fi[2] in fj:
            continue
        if triangles_intersect(mesh.triangle(i), mesh.triangle(j), eps):
            hits.append((i, j))
    return hits
