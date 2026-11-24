"""Topological analysis: shells, winding consistency, boundary loops, volume."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .mesh import Edge, EdgeMap, Mesh
from .vec import Vec3, cross, dot, length, sub


@dataclass
class Shell:
    faces: list[int]
    signed_volume: float
    area: float
    closed: bool
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]

    @property
    def size(self) -> float:
        return max(self.hi[i] - self.lo[i] for i in range(3))


@dataclass
class Orientation:
    """Result of propagating a consistent winding across manifold edges.

    ``flip[f]`` is True for faces that disagree with the majority winding of their shell (those
    are the faces a repair flips). ``inconsistent_edges`` are manifold edges walked in the same
    direction by both faces. ``conflicts`` counts edges where propagation met a contradiction
    (non-orientable surfaces such as a Moebius strip).
    """

    flip: list[bool]
    inconsistent_edges: list[Edge] = field(default_factory=list)
    conflicts: int = 0


@dataclass
class BoundaryLoop:
    vertices: list[int]
    closed: bool
    perimeter: float


def manifold_neighbours(mesh: Mesh, edges: EdgeMap) -> list[list[tuple[int, bool]]]:
    """For each face, neighbours across edges shared by exactly two faces.

    The bool is True when both faces walk the shared edge in the same direction (inconsistent).
    """
    nbrs: list[list[tuple[int, bool]]] = [[] for _ in mesh.faces]
    for users in edges.values():
        if len(users) == 2:
            (f1, d1), (f2, d2) = users
            same = d1 == d2
            nbrs[f1].append((f2, same))
            nbrs[f2].append((f1, same))
    return nbrs


def shells(mesh: Mesh, edges: EdgeMap, excluded: set[int]) -> list[Shell]:
    """Connected components through manifold edges (non-manifold edges do not join shells)."""
    nbrs = manifold_neighbours(mesh, edges)
    seen = [False] * len(mesh.faces)
    result: list[Shell] = []
    for start in range(len(mesh.faces)):
        if seen[start] or start in excluded:
            continue
        seen[start] = True
        comp = [start]
        queue = deque([start])
        while queue:
            f = queue.popleft()
            for g, _ in nbrs[f]:
                if not seen[g]:
                    seen[g] = True
                    comp.append(g)
                    queue.append(g)
        comp.sort()
        # Closed: every edge of every face is shared with exactly one other face.
        closed = all(len(nbrs[f]) == 3 for f in comp)
        vol = sum(signed_volume_of_face(mesh, f) for f in comp)
        area = sum(mesh.face_area(f) for f in comp)
        pts = [mesh.vertices[i] for f in comp for i in mesh.faces[f]]
        lo = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts))
        hi = (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
        result.append(Shell(comp, vol, area, closed, lo, hi))
    return result


def signed_volume_of_face(mesh: Mesh, f: int) -> float:
    a, b, c = mesh.triangle(f)
    return dot(a, cross(b, c)) / 6.0


def signed_volume(mesh: Mesh, faces: list[int] | None = None) -> float:
    ids = range(len(mesh.faces)) if faces is None else faces
    return sum(signed_volume_of_face(mesh, f) for f in ids)


def orientation(mesh: Mesh, edges: EdgeMap, excluded: set[int]) -> Orientation:
    """Breadth-first winding propagation per shell, keeping the majority winding of each shell."""
    nbrs = manifold_neighbours(mesh, edges)
    n = len(mesh.faces)
    state = [-1] * n  # 0 = same as seed, 1 = opposite of seed
    flip = [False] * n
    conflicts = 0
    for start in range(n):
        if state[start] >= 0 or start in excluded:
            continue
        state[start] = 0
        comp = [start]
        queue = deque([start])
        while queue:
            f = queue.popleft()
            for g, same in nbrs[f]:
                want = state[f] ^ int(same)
                if state[g] < 0:
                    state[g] = want
                    comp.append(g)
                    queue.append(g)
                elif state[g] != want and f < g:
                    conflicts += 1
        ones = sum(state[f] for f in comp)
        majority = 1 if ones * 2 > len(comp) else 0  # a tie keeps the seed's winding
        for f in comp:
            flip[f] = state[f] != majority
    inconsistent = sorted(
        e for e, users in edges.items() if len(users) == 2 and users[0][1] == users[1][1]
    )
    return Orientation(flip, inconsistent, conflicts)


def boundary_loops(mesh: Mesh, edges: EdgeMap) -> list[BoundaryLoop]:
    """Chain boundary edges (used by one face) into loops, following each face's direction."""
    out: dict[int, list[int]] = {}
    count = 0
    for (u, v), users in edges.items():
        if len(users) != 1:
            continue
        _, d = users[0]
        a, b = (u, v) if d > 0 else (v, u)
        out.setdefault(a, []).append(b)
        count += 1
    loops: list[BoundaryLoop] = []
    for start in sorted(out):
        while out.get(start):
            chain = [start]
            cur = start
            closed = False
            while True:
                nxt_list = out.get(cur)
                if not nxt_list:
                    break  # dead end: open chain (only with inconsistent winding)
                nxt = nxt_list.pop(0)
                if nxt == start:
                    closed = True
                    break
                chain.append(nxt)
                cur = nxt
            steps = len(chain) if closed else len(chain) - 1
            per = sum(length(_d(mesh, chain[i], chain[(i + 1) % len(chain)])) for i in range(steps))
            loops.append(BoundaryLoop(chain, closed, per))
    return loops


def _d(mesh: Mesh, a: int, b: int) -> tuple[float, float, float]:
    pa, pb = mesh.vertices[a], mesh.vertices[b]
    return (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])


def winding_number(mesh: Mesh, faces: list[int], p: Vec3) -> float:
    """Generalised winding number of point ``p`` with respect to the triangles ``faces``.

    For a closed shell with outward normals it is 1 inside and 0 outside (-1 inside when the
    normals point inward). Each triangle contributes its signed solid angle seen from ``p``
    (Van Oosterom and Strackee, 1983), divided by 4 pi.
    """
    total = 0.0
    for f in faces:
        a, b, c = (sub(q, p) for q in mesh.triangle(f))
        la, lb, lc = length(a), length(b), length(c)
        num = dot(a, cross(b, c))
        den = la * lb * lc + dot(a, b) * lc + dot(b, c) * la + dot(c, a) * lb
        total += 2.0 * math.atan2(num, den)
    return total / (4.0 * math.pi)


@dataclass
class Nesting:
    """How closed shells sit inside each other.

    ``parent[i]`` is the index of the smallest closed shell that contains shell ``i`` (``None`` for
    a shell that is not inside another one, and for open shells). ``role[i]`` is one of:

    * ``"body"``: a solid whose winding is correct for its depth (outward normals at even depth),
    * ``"void"``: a correctly wound cavity inside a body (inward normals),
    * ``"embedded"``: an outward shell inside a body without a void between them (a separate solid
      buried in another; slicers merge it, and the summed volume counts the overlap twice),
    * ``"inside_out"``: a shell whose winding is wrong for its depth,
    * ``"open"``: not closed, so inside and outside are undefined.
    """

    parent: list[int | None]
    role: list[str]

    @property
    def inverted(self) -> list[int]:
        return [i for i, r in enumerate(self.role) if r == "inside_out"]


def _inside_box(inner: Shell, outer: Shell, eps: float) -> bool:
    return all(
        inner.lo[k] >= outer.lo[k] - eps and inner.hi[k] <= outer.hi[k] + eps for k in range(3)
    )


def _probe_point(mesh: Mesh, shell: Shell) -> Vec3:
    """Centroid of the shell's largest face: a point on the shell away from its edges."""
    best = max(shell.faces, key=mesh.face_area)
    a, b, c = mesh.triangle(best)
    return ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3)


def nesting(mesh: Mesh, shell_list: list[Shell]) -> Nesting:
    """Classify each shell from its signed volume *and* the shells that contain it.

    A closed shell is inside another when its bounding box lies within the other's and a point
    on it has a winding number of magnitude above one half with respect to the other shell. The
    winding a shell should have follows from its parent: the outermost shells should enclose
    positive volume, a cavity inside a body negative volume, an island inside a cavity positive
    again. A correctly wound void is therefore *not* inside out, and an inside-out file (every
    shell reversed) is still recognised as such.
    """
    n = len(shell_list)
    parent: list[int | None] = [None] * n
    role = ["open"] * n
    closed = [i for i in range(n) if shell_list[i].closed]
    vol = {i: signed_volume(mesh, shell_list[i].faces) for i in closed}
    box = {
        i: math.prod(max(shell_list[i].hi[k] - shell_list[i].lo[k], 0.0) for k in range(3))
        for i in closed
    }
    for i in closed:
        s = shell_list[i]
        eps = 1e-9 * max(1.0, s.size)
        probe: Vec3 | None = None
        for j in closed:
            if j == i or box[j] < box[i] or not _inside_box(s, shell_list[j], eps):
                continue
            if box[j] == box[i] and j > i:
                continue  # identical boxes: only the earlier shell may contain the later one
            if parent[i] is not None and box[j] >= box[parent[i]]:  # type: ignore[index]
                continue  # already inside a smaller container
            if probe is None:
                probe = _probe_point(mesh, s)
            if abs(winding_number(mesh, shell_list[j].faces, probe)) > 0.5:
                parent[i] = j
    # Decide roles outermost first, so every parent is classified before its children.
    expected: dict[int, int] = {}
    for i in sorted(closed, key=lambda k: -box[k]):
        actual = 1 if vol[i] >= 0 else -1
        p = parent[i]
        if p is None:
            want = 1
        else:
            want = -expected[p]
            if actual == 1 and role[p] in ("body", "embedded"):
                role[i] = "embedded"
                expected[i] = 1
                continue
        expected[i] = want
        if actual != want:
            role[i] = "inside_out"
        else:
            role[i] = "body" if want == 1 else "void"
    return Nesting(parent, role)
