"""Printability heuristics: overhangs, thin walls and a best-orientation search.

These are geometric estimates, not slicer simulations. In particular the orientation search only
minimises downward-facing area steeper than the overhang limit; it knows nothing about bridging,
supports touching the model, surface finish, strength along layers or bed adhesion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .grid import TriangleGrid, ray_triangle
from .mesh import Mesh
from .vec import Vec3, dot, normalize, rotation_to_z, scale, sub

try:  # optional speed-up for the orientation search
    import numpy as _np
except ImportError:  # pragma: no cover - exercised only where numpy is missing
    _np = None  # type: ignore[assignment]


@dataclass
class OverhangResult:
    faces: list[int]
    area: float
    projected_area: float  # area projected onto the bed plane: an estimate of support footprint


@dataclass
class ThinWallResult:
    faces: list[int]
    thickness: dict[int, float]
    area: float
    threshold: float

    @property
    def min_thickness(self) -> float | None:
        return min(self.thickness.values()) if self.thickness else None


@dataclass
class OrientationCandidate:
    up: Vec3
    rot_x: float
    rot_y: float
    overhang_area: float
    contact_area: float
    height: float


def bed_tolerance(mesh: Mesh) -> float:
    lo, hi = mesh.bounds()
    return max(1e-4, 1e-6 * max(hi[i] - lo[i] for i in range(3)))


def overhangs(mesh: Mesh, faces: list[int], angle_deg: float) -> OverhangResult:
    """Downward faces tilted more than ``angle_deg`` away from vertical, ignoring faces on the bed.

    A face is an overhang when its unit normal has ``n_z < -sin(angle)``: 0 deg is a vertical wall,
    90 deg a flat ceiling. Faces whose three corners all lie on the lowest Z (within a small
    tolerance) rest on the bed and are excluded.
    """
    limit = -math.sin(math.radians(angle_deg))
    lo, _ = mesh.bounds()
    tol = bed_tolerance(mesh)
    hits: list[int] = []
    area = 0.0
    projected = 0.0
    for f in faces:
        n = mesh.face_normal(f)
        if n[2] >= limit:
            continue
        a, b, c = mesh.triangle(f)
        if max(a[2], b[2], c[2]) <= lo[2] + tol:
            continue
        hits.append(f)
        fa = mesh.face_area(f)
        area += fa
        projected += fa * -n[2]
    return OverhangResult(hits, area, projected)


def thin_walls(
    mesh: Mesh,
    faces: list[int],
    threshold: float,
    grid: TriangleGrid | None = None,
    max_tilt_deg: float = 45.0,
) -> ThinWallResult:
    """Faces of near-vertical walls whose opposite wall is closer than ``threshold`` mm.

    From each face centroid a ray is cast inward (along the negated normal); the nearest other
    triangle hit within ``threshold`` gives the local wall thickness. Only faces whose normal is
    within ``max_tilt_deg`` of horizontal are tested, because the nozzle width limits wall thickness
    in XY; thin horizontal layers are a layer-height question instead. Normals must point outward
    (fix winding first), otherwise the ray goes the wrong way and the wall is missed.
    """
    if grid is None:
        grid = TriangleGrid(mesh, faces)
    max_nz = math.sin(math.radians(max_tilt_deg))
    found: dict[int, float] = {}
    for f in faces:
        n = mesh.face_normal(f)
        if n == (0.0, 0.0, 0.0) or abs(n[2]) > max_nz:
            continue
        origin = mesh.face_centroid(f)
        direction = scale(n, -1.0)
        end = sub(origin, scale(n, threshold))
        lo = (min(origin[0], end[0]), min(origin[1], end[1]), min(origin[2], end[2]))
        hi = (max(origin[0], end[0]), max(origin[1], end[1]), max(origin[2], end[2]))
        best = threshold
        for g in grid.query(lo, hi):
            if g == f:
                continue
            t = ray_triangle(origin, direction, *mesh.triangle(g))
            if t is not None and 1e-6 < t < best:
                best = t
        if best < threshold:
            found[f] = best
    area = sum(mesh.face_area(f) for f in found)
    return ThinWallResult(sorted(found), found, area, threshold)


def fibonacci_directions(count: int) -> list[Vec3]:
    """``count`` roughly uniform unit vectors on the sphere plus the six axis directions."""
    dirs: list[Vec3] = [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)]
    dirs += [(-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0)]
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(count):
        z = 1.0 - 2.0 * (i + 0.5) / count
        r = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden * i
        dirs.append((r * math.cos(theta), r * math.sin(theta), z))
    return dirs


def _score_pure(
    normals: list[Vec3],
    areas: list[float],
    corners: list[tuple[Vec3, Vec3, Vec3]],
    up: Vec3,
    limit: float,
    tol: float,
) -> tuple[float, float, float]:
    heights = [(dot(a, up), dot(b, up), dot(c, up)) for a, b, c in corners]
    low = min(min(h) for h in heights)
    high = max(max(h) for h in heights)
    overhang = 0.0
    contact = 0.0
    for n, area, h in zip(normals, areas, heights, strict=True):
        on_bed = max(h) <= low + tol
        if on_bed:
            contact += area
        elif dot(n, up) < limit:
            overhang += area
    return overhang, contact, high - low


def _score_numpy(
    normals: list[Vec3],
    areas: list[float],
    corners: list[tuple[Vec3, Vec3, Vec3]],
    ups: list[Vec3],
    limit: float,
    tol: float,
) -> list[tuple[float, float, float]]:
    assert _np is not None
    np = _np
    n_arr = np.asarray(normals, dtype=float)  # (F, 3)
    a_arr = np.asarray(areas, dtype=float)  # (F,)
    c_arr = np.asarray(corners, dtype=float)  # (F, 3, 3)
    u_arr = np.asarray(ups, dtype=float)  # (D, 3)
    out: list[tuple[float, float, float]] = []
    for u in u_arr:
        h = c_arr @ u  # (F, 3)
        low = float(h.min())
        high = float(h.max())
        on_bed = h.max(axis=1) <= low + tol
        down = (n_arr @ u) < limit
        overhang = float(a_arr[down & ~on_bed].sum())
        contact = float(a_arr[on_bed].sum())
        out.append((overhang, contact, high - low))
    return out


def best_orientations(
    mesh: Mesh,
    faces: list[int],
    angle_deg: float = 45.0,
    samples: int = 200,
    use_numpy: bool | None = None,
) -> list[OrientationCandidate]:
    """Score sampled "up" directions; return candidates sorted best first.

    Ranking: orientations with a face on the bed first, then least overhang area, then largest
    bed contact area, then lowest height. This is a
    sampling heuristic: the true optimum can lie between samples.
    """
    limit = -math.sin(math.radians(angle_deg))
    tol = bed_tolerance(mesh)
    normals = [mesh.face_normal(f) for f in faces]
    areas = [mesh.face_area(f) for f in faces]
    corners = [mesh.triangle(f) for f in faces]
    ups = [normalize(u) for u in fibonacci_directions(samples)]
    if use_numpy is None:
        use_numpy = _np is not None
    if use_numpy and _np is not None:
        scores = _score_numpy(normals, areas, corners, ups, limit, tol)
    else:
        scores = [_score_pure(normals, areas, corners, u, limit, tol) for u in ups]
    result = []
    for u, (overhang, contact, height) in zip(ups, scores, strict=True):
        rx, ry = rotation_to_z(u)
        result.append(OrientationCandidate(u, rx, ry, overhang, contact, height))
    # Round scores so float noise does not decide between equivalent orientations.
    # Orientations that rest on a vertex or an edge (no face on the bed) rank last.
    result.sort(
        key=lambda c: (
            c.contact_area <= 0.0,
            round(c.overhang_area, 6),
            -round(c.contact_area, 6),
            round(c.height, 6),
        )
    )
    return result


def numpy_available() -> bool:
    return _np is not None
