"""Opt-in repairs. The input mesh is never modified; a new mesh and a change log are returned."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .mesh import Face, Mesh, edge_map
from .topology import boundary_loops, nesting, orientation, shells
from .vec import Vec3, cross, dot, length, normalize, sub


def _n(count: int, noun: str) -> str:
    if count == 1:
        return f"1 {noun}"
    plural = noun[:-2] + "ices" if noun.endswith("vertex") else noun + "s"
    return f"{count} {plural}"


@dataclass
class RepairLog:
    input_faces: int = 0
    output_faces: int = 0
    near_duplicate_vertices_merged: int = 0
    degenerate_removed: int = 0
    duplicates_removed: int = 0
    faces_flipped: int = 0
    shells_inverted: int = 0
    holes_filled: int = 0
    triangles_added: int = 0
    holes_skipped: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"faces: {self.input_faces} in, {self.output_faces} out",
            f"welded {_n(self.near_duplicate_vertices_merged, 'near-duplicate vertex')} "
            "(beyond exact matches)",
            f"removed {_n(self.degenerate_removed, 'degenerate face')}",
            f"removed {_n(self.duplicates_removed, 'duplicate face')}",
            f"flipped {_n(self.faces_flipped, 'face')} to make winding consistent",
            f"inverted {_n(self.shells_inverted, 'inside-out shell')}",
            f"filled {_n(self.holes_filled, 'hole')} with {_n(self.triangles_added, 'triangle')}",
        ]
        out += [f"skipped hole: {reason}" for reason in self.holes_skipped]
        out.append("normals recomputed from winding for every face")
        return out

    def to_dict(self) -> dict[str, object]:
        return {
            "input_faces": self.input_faces,
            "output_faces": self.output_faces,
            "near_duplicate_vertices_merged": self.near_duplicate_vertices_merged,
            "degenerate_removed": self.degenerate_removed,
            "duplicates_removed": self.duplicates_removed,
            "faces_flipped": self.faces_flipped,
            "shells_inverted": self.shells_inverted,
            "holes_filled": self.holes_filled,
            "triangles_added": self.triangles_added,
            "holes_skipped": list(self.holes_skipped),
            "normals_recomputed": True,
        }


def _newell(points: list[Vec3]) -> Vec3:
    nx = ny = nz = 0.0
    n = len(points)
    for i in range(n):
        x1, y1, z1 = points[i]
        x2, y2, z2 = points[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return (nx, ny, nz)


def _cross2(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def ear_clip(points2d: list[tuple[float, float]]) -> list[tuple[int, int, int]] | None:
    """Triangulate a simple counter-clockwise polygon; returns index triples or ``None``.

    O(n^3) worst case, which is fine for the small holes this tool fills.
    """
    idx = list(range(len(points2d)))
    tris: list[tuple[int, int, int]] = []
    span = max(max(abs(p[0]), abs(p[1])) for p in points2d) or 1.0
    eps = 1e-12 * span * span
    guard = 0
    while len(idx) > 3:
        guard += 1
        if guard > 10 * len(points2d) + 10:
            return None
        clipped = False
        m = len(idx)
        for k in range(m):
            i_prev, i_cur, i_next = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = points2d[i_prev], points2d[i_cur], points2d[i_next]
            if _cross2(a, b, c) <= eps:
                continue  # reflex or collinear corner
            inside = False
            for j in idx:
                if j in (i_prev, i_cur, i_next):
                    continue
                p = points2d[j]
                if (
                    _cross2(a, b, p) >= -eps
                    and _cross2(b, c, p) >= -eps
                    and _cross2(c, a, p) >= -eps
                ):
                    inside = True
                    break
            if inside:
                continue
            tris.append((i_prev, i_cur, i_next))
            idx.pop(k)
            clipped = True
            break
        if not clipped:
            return None
    a, b, c = (points2d[i] for i in idx)
    if _cross2(a, b, c) <= eps:
        return None
    tris.append((idx[0], idx[1], idx[2]))
    return tris


def fill_loop(
    vertices: list[Vec3], loop: list[int], planar_tolerance: float
) -> tuple[list[Face] | None, str]:
    """Faces closing a boundary loop (given in the direction the existing faces walk it).

    The new faces walk each boundary edge in the opposite direction, so the patch continues the
    surrounding winding. Returns ``(None, reason)`` when the hole is not simple and planar.
    """
    poly = list(reversed(loop))
    pts = [vertices[i] for i in poly]
    if len(pts) < 3:
        return None, f"loop of {len(pts)} vertices"
    if len(set(poly)) != len(poly):
        return None, f"loop of {len(poly)} edges touches itself"
    n = _newell(pts)
    if length(n) == 0.0:
        return None, "loop has zero area"
    n = normalize(n)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    cz = sum(p[2] for p in pts) / len(pts)
    c = (cx, cy, cz)
    size = max(length(sub(p, c)) for p in pts)
    deviation = max(abs(dot(sub(p, c), n)) for p in pts)
    if deviation > planar_tolerance * size + 1e-9:
        return None, (
            f"loop of {len(poly)} edges is not planar (deviation {deviation:.3f} mm "
            f"around {cx:.2f}, {cy:.2f}, {cz:.2f})"
        )
    helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = normalize(cross(helper, n))
    v = cross(n, u)
    flat = [(dot(sub(p, c), u), dot(sub(p, c), v)) for p in pts]
    tris = ear_clip(flat)
    if tris is None:
        return None, f"loop of {len(poly)} edges could not be triangulated (self-overlapping)"
    return [(poly[a], poly[b], poly[c2]) for a, b, c2 in tris], ""


def repair(
    mesh: Mesh, fill_holes: bool = True, planar_tolerance: float = 0.01
) -> tuple[Mesh, RepairLog]:
    log = RepairLog(input_faces=len(mesh.faces))
    log.near_duplicate_vertices_merged = max(0, mesh.exact_unique_vertices - len(mesh.vertices))
    lo, hi = mesh.bounds()
    diag = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
    area_eps = max(1e-12, 1e-10 * diag * diag)

    faces: list[Face] = []
    seen: set[tuple[int, ...]] = set()
    for f, face in enumerate(mesh.faces):
        if mesh.is_collapsed(f) or mesh.face_area(f) <= area_eps:
            log.degenerate_removed += 1
            continue
        key = tuple(sorted(face))
        if key in seen:
            log.duplicates_removed += 1
            continue
        seen.add(key)
        faces.append(face)

    work = Mesh(list(mesh.vertices), faces, [(0.0, 0.0, 0.0)] * len(faces), mesh.weld_tolerance)
    orient = orientation(work, edge_map(work), set())
    for f, flip in enumerate(orient.flip):
        if flip:
            a, b, c = work.faces[f]
            work.faces[f] = (a, c, b)
            log.faces_flipped += 1

    if fill_holes:
        for loop in boundary_loops(work, edge_map(work)):
            if not loop.closed:
                log.holes_skipped.append(f"open chain of {len(loop.vertices)} vertices")
                continue
            new, reason = fill_loop(work.vertices, loop.vertices, planar_tolerance)
            if new is None:
                log.holes_skipped.append(reason)
                continue
            work.faces.extend(new)
            log.holes_filled += 1
            log.triangles_added += len(new)

    # Invert only shells wound wrongly for where they sit; a cavity inside a body keeps its
    # inward normals (inverting it would fill the cavity in).
    shell_list = shells(work, edge_map(work), set())
    for i in nesting(work, shell_list).inverted:
        log.shells_inverted += 1
        for f in shell_list[i].faces:
            a, b, c = work.faces[f]
            work.faces[f] = (a, c, b)

    used = sorted({i for face in work.faces for i in face})
    index = {old: new for new, old in enumerate(used)}
    out = Mesh(
        vertices=[work.vertices[i] for i in used],
        faces=[(index[a], index[b], index[c]) for a, b, c in work.faces],
        stored_normals=[],
        weld_tolerance=mesh.weld_tolerance,
        exact_unique_vertices=len(used),
        source_format="repaired",
        name=mesh.name,
    )
    out.stored_normals = [out.face_normal(f) for f in range(len(out.faces))]
    out.source_count = len(out.faces)
    log.output_faces = len(out.faces)
    return out, log
