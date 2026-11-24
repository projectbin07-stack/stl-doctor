"""Run every check on a mesh and collect findings into a report."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from . import __version__
from .grid import TriangleGrid
from .intersect import self_intersections
from .mesh import Edge, EdgeMap, Mesh, edge_map
from .printability import (
    OrientationCandidate,
    OverhangResult,
    ThinWallResult,
    best_orientations,
    overhangs,
    thin_walls,
)
from .printers import DEFAULT_PRINTER, PRINTERS, Bed
from .topology import (
    BoundaryLoop,
    Nesting,
    Orientation,
    Shell,
    boundary_loops,
    nesting,
    orientation,
    shells,
    signed_volume,
)
from .vec import Vec3, dot, normalize

ERROR = "error"
WARNING = "warning"
INFO = "info"
_RANK = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Options:
    bed: Bed = field(default_factory=lambda: PRINTERS[DEFAULT_PRINTER])
    nozzle: float = 0.4
    thin_factor: float = 2.0
    overhang_angle: float = 45.0
    tiny_shell: float = 1.0  # mm: shells whose largest dimension is below this are "tiny"
    normal_tolerance_deg: float = 45.0
    check_intersections: bool = True
    check_thin_walls: bool = True
    suggest_orientation: bool = True
    orientation_samples: int = 100
    max_locations: int = 50


@dataclass
class Finding:
    code: str
    severity: str
    title: str
    count: int
    message: str
    locations: list[dict[str, Any]] = field(default_factory=list)
    faces: list[int] = field(default_factory=list)
    edges: list[tuple[Vec3, Vec3]] = field(default_factory=list)

    def to_dict(self, max_locations: int) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "count": self.count,
            "message": self.message,
            "locations": self.locations[:max_locations],
            "locations_truncated": len(self.locations) > max_locations,
        }


@dataclass
class Report:
    path: str
    mesh: Mesh
    options: Options
    stats: dict[str, Any]
    findings: list[Finding]
    shells: list[Shell]
    overhang: OverhangResult
    thin: ThinWallResult | None
    orientation: list[OrientationCandidate]
    boundary_edges: list[tuple[Vec3, Vec3]]
    non_manifold_edges: list[tuple[Vec3, Vec3]]
    problem_faces: set[int]
    shell_roles: list[str] = field(default_factory=list)
    shell_parents: list[int | None] = field(default_factory=list)
    # One entry per object of a multi-object file (3MF build items), each checked on its own.
    objects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        sev = {f.severity for f in self.findings}
        if ERROR in sev:
            return "fail"
        if WARNING in sev:
            return "warn"
        return "pass"

    def exit_code(self, strict: bool = False) -> int:
        v = self.verdict
        if v == "fail":
            return 2
        if v == "warn" and strict:
            return 1
        return 0

    def finding(self, code: str) -> Finding | None:
        for f in self.findings:
            if f.code == code:
                return f
        return None

    def counts(self) -> dict[str, int]:
        out = {ERROR: 0, WARNING: 0, INFO: 0}
        for f in self.findings:
            out[f.severity] += 1
        return out

    def summary(self) -> str:
        c = self.counts()
        return (
            f"{_plural(c[ERROR], 'error')}, {_plural(c[WARNING], 'warning')}, "
            f"{_plural(c[INFO], 'note')}"
        )

    def to_dict(self) -> dict[str, Any]:
        opts = self.options
        best = self.orientation[0] if self.orientation else None
        return {
            "tool": "stl-doctor",
            "version": __version__,
            "file": self.path,
            "verdict": self.verdict,
            "counts": self.counts(),
            "options": {
                "printer": opts.bed.name,
                "bed_mm": [opts.bed.x, opts.bed.y, opts.bed.z],
                "nozzle_mm": opts.nozzle,
                "thin_wall_threshold_mm": opts.nozzle * opts.thin_factor,
                "overhang_angle_deg": opts.overhang_angle,
                "weld_tolerance_mm": self.mesh.weld_tolerance,
            },
            "stats": self.stats,
            "shells": [
                {
                    "faces": len(s.faces),
                    "closed": s.closed,
                    "signed_volume_mm3": _r(s.signed_volume),
                    "area_mm2": _r(s.area),
                    "min": _pt(s.lo),
                    "max": _pt(s.hi),
                    "role": self.shell_roles[i] if i < len(self.shell_roles) else "",
                    "inside": self.shell_parents[i] if i < len(self.shell_parents) else None,
                }
                for i, s in enumerate(self.shells)
            ],
            "findings": [f.to_dict(opts.max_locations) for f in self.findings],
            "objects": self.objects,
            "orientation": None
            if best is None
            else {
                "heuristic": True,
                "current_overhang_mm2": _r(self.overhang.area),
                "best_up_vector": [_r(c) for c in best.up],
                "rotate_x_deg": _r(best.rot_x, 2),
                "then_rotate_y_deg": _r(best.rot_y, 2),
                "overhang_mm2": _r(best.overhang_area),
                "bed_contact_mm2": _r(best.contact_area),
                "height_mm": _r(best.height),
            },
        }


def _r(x: float, nd: int = 4) -> float:
    return round(x, nd) + 0.0  # + 0.0 turns -0.0 into 0.0


def _pt(p: Vec3) -> list[float]:
    return [_r(p[0]), _r(p[1]), _r(p[2])]


def _loc(p: Vec3, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"x": _r(p[0]), "y": _r(p[1]), "z": _r(p[2])}
    d.update(extra)
    return d


def _mid(a: Vec3, b: Vec3) -> Vec3:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)


def _edge_points(mesh: Mesh, e: Edge) -> tuple[Vec3, Vec3]:
    return mesh.vertices[e[0]], mesh.vertices[e[1]]


def _with_flipped(mesh: Mesh, faces: list[int]) -> Mesh:
    """Shallow copy of ``mesh`` with the winding of ``faces`` reversed."""
    if not faces:
        return mesh
    new_faces = list(mesh.faces)
    for f in faces:
        a, b, c = new_faces[f]
        new_faces[f] = (a, c, b)
    return replace(mesh, faces=new_faces)


def _v(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def analyze(mesh: Mesh, options: Options | None = None, path: str = "") -> Report:
    opts = options or Options()
    findings: list[Finding] = []
    n_faces = len(mesh.faces)
    lo, hi = mesh.bounds()
    dims = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    diag = math.sqrt(sum(d * d for d in dims))
    area_eps = max(1e-12, 1e-10 * diag * diag)

    if mesh.non_finite:
        findings.append(
            Finding(
                "non_finite_vertices",
                ERROR,
                "NaN or infinite coordinates",
                len(mesh.non_finite),
                f"{_plural(len(mesh.non_finite), 'facet')} in the file had NaN/inf coordinates "
                "and were dropped before analysis (facet numbers are 0-based).",
                [{"facet": i} for i in mesh.non_finite],
            )
        )

    # Degenerate faces: collapsed by welding, or (near) zero area.
    collapsed = {f for f in range(n_faces) if mesh.is_collapsed(f)}
    areas = [0.0 if f in collapsed else mesh.face_area(f) for f in range(n_faces)]
    degenerate = sorted(f for f in range(n_faces) if f in collapsed or areas[f] <= area_eps)
    if degenerate:
        findings.append(
            Finding(
                "degenerate_faces",
                WARNING,
                "Degenerate (zero-area) faces",
                len(degenerate),
                f"{_plural(len(degenerate), 'face')} with zero area or two corners welded "
                "together. "
                "Slicers usually ignore them; `stl-doctor repair` removes them.",
                [_loc(mesh.face_centroid(f), face=f) for f in degenerate],
                degenerate,
            )
        )

    # Duplicate faces: same three vertices (either winding). Extra copies leave the topology.
    seen: dict[tuple[int, ...], int] = {}
    duplicates: list[int] = []
    for f in range(n_faces):
        if f in collapsed:
            continue
        key = tuple(sorted(mesh.faces[f]))
        if key in seen:
            duplicates.append(f)
        else:
            seen[key] = f
    if duplicates:
        findings.append(
            Finding(
                "duplicate_faces",
                WARNING,
                "Duplicate faces",
                len(duplicates),
                f"{_plural(len(duplicates), 'face')} "
                f"{_v(len(duplicates), 'repeats', 'repeat')}"
                f" the vertices of an earlier face.",
                [
                    _loc(
                        mesh.face_centroid(f),
                        face=f,
                        duplicate_of=seen[tuple(sorted(mesh.faces[f]))],
                    )
                    for f in duplicates
                ],
                duplicates,
            )
        )
    excluded = collapsed | set(duplicates)
    active = [f for f in range(n_faces) if f not in excluded]
    geometric = [f for f in active if areas[f] > area_eps]

    edges: EdgeMap = edge_map(mesh, excluded)
    non_manifold = sorted(e for e, users in edges.items() if len(users) > 2)
    nm_edges = [_edge_points(mesh, e) for e in non_manifold]
    if non_manifold:
        findings.append(
            Finding(
                "non_manifold_edges",
                ERROR,
                "Non-manifold edges",
                len(non_manifold),
                f"{_plural(len(non_manifold), 'edge')} "
                f"{_v(len(non_manifold), 'is', 'are')}"
                " shared by more than two faces. Slicers may guess the inside/outside wrongly "
                "here; separate the bodies or merge them properly.",
                [_loc(_mid(*_edge_points(mesh, e)), faces=len(edges[e])) for e in non_manifold],
                sorted({f for e in non_manifold for f, _ in edges[e]}),
                nm_edges,
            )
        )

    boundary = [e for e, users in edges.items() if len(users) == 1]
    b_edges = [_edge_points(mesh, e) for e in sorted(boundary)]
    loops: list[BoundaryLoop] = boundary_loops(mesh, edges)
    if boundary:
        loop_locs = []
        for lp in sorted(loops, key=lambda x: -x.perimeter):
            pts = [mesh.vertices[v] for v in lp.vertices]
            c = (
                sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts),
                sum(p[2] for p in pts) / len(pts),
            )
            loop_locs.append(
                _loc(
                    c,
                    edges=len(lp.vertices) if lp.closed else len(lp.vertices) - 1,
                    perimeter_mm=_r(lp.perimeter, 3),
                    closed_loop=lp.closed,
                )
            )
        findings.append(
            Finding(
                "open_boundaries",
                ERROR,
                "Holes (open boundaries)",
                len(loops),
                f"{_plural(len(loops), 'hole')} bounded by {_plural(len(boundary), 'edge')}; "
                f"total perimeter {sum(lp.perimeter for lp in loops):.2f} mm. The mesh is not "
                "watertight, so volume and inside/outside are unreliable.",
                loop_locs,
                sorted({users[0][0] for e, users in edges.items() if len(users) == 1}),
                b_edges,
            )
        )

    orient: Orientation = orientation(mesh, edges, excluded)
    flipped = [f for f in active if orient.flip[f]]
    if orient.inconsistent_edges:
        msg = (
            f"{_plural(len(orient.inconsistent_edges), 'edge')} joined faces with opposite "
            f"winding; {_plural(len(flipped), 'face')} "
            f"{_v(len(flipped), 'disagrees', 'disagree')}"
            f" with the rest of the shell "
            "(their normals point the wrong way)."
        )
        if orient.conflicts:
            msg += f" The surface is non-orientable ({orient.conflicts} conflicts)."
        findings.append(
            Finding(
                "inconsistent_winding",
                ERROR,
                "Inconsistent winding / flipped faces",
                len(flipped),
                msg,
                [_loc(mesh.face_centroid(f), face=f) for f in flipped],
                flipped,
                [_edge_points(mesh, e) for e in orient.inconsistent_edges],
            )
        )

    shell_list = shells(mesh, edges, excluded)
    shell_list.sort(key=lambda s: -abs(s.signed_volume))
    # Printability checks run on a copy whose winding is corrected (flipped faces turned back,
    # inside-out shells inverted), so one bad face does not show up as a fake overhang. Whether a
    # closed shell is inside out depends on what contains it: a cavity inside a body is correctly
    # wound with inward normals and negative volume.
    pmesh = _with_flipped(mesh, flipped)
    nest: Nesting = nesting(pmesh, shell_list)
    inverted = [shell_list[i] for i in nest.inverted]
    if inverted:
        pmesh = _with_flipped(pmesh, [f for s in inverted for f in s.faces])
        findings.append(
            Finding(
                "inside_out",
                WARNING,
                "Inside-out shells",
                len(inverted),
                f"{_plural(len(inverted), 'closed shell')} "
                f"{_v(len(inverted), 'is', 'are')} wound the wrong way for where "
                f"{_v(len(inverted), 'it sits', 'they sit')}: an outer body with inward "
                "normals (negative volume), or a cavity inside a body with outward normals. "
                "Most slicers flip outer bodies automatically; `stl-doctor repair` fixes the file.",
                [_loc(_mid(s.lo, s.hi), volume_mm3=_r(s.signed_volume)) for s in inverted],
                [f for s in inverted for f in s.faces],
            )
        )
    voids = [shell_list[i] for i, r in enumerate(nest.role) if r == "void"]
    embedded = [shell_list[i] for i, r in enumerate(nest.role) if r == "embedded"]
    if voids:
        findings.append(
            Finding(
                "internal_voids",
                INFO,
                "Internal voids",
                len(voids),
                f"{_plural(len(voids), 'closed shell')} "
                f"{_v(len(voids), 'is a cavity', 'are cavities')} inside a body, wound with "
                "inward normals as a cavity should be. The slicer leaves it empty and the "
                "volume already excludes it; `stl-doctor repair` keeps it.",
                [_loc(_mid(s.lo, s.hi), volume_mm3=_r(s.signed_volume)) for s in voids],
            )
        )
    if embedded:
        findings.append(
            Finding(
                "embedded_shells",
                INFO,
                "Bodies inside other bodies",
                len(embedded),
                f"{_plural(len(embedded), 'closed shell')} "
                f"{_v(len(embedded), 'lies', 'lie')} entirely inside another body. Slicers "
                "merge them, so they add nothing to the print, but the reported volume counts "
                "them twice.",
                [_loc(_mid(s.lo, s.hi), volume_mm3=_r(s.signed_volume)) for s in embedded],
            )
        )
    if len(shell_list) > 1:
        n_bodies = len(shell_list) - len(voids)
        findings.append(
            Finding(
                "multiple_shells",
                INFO,
                "Several shells",
                len(shell_list),
                f"The file contains {len(shell_list)} separate shells "
                f"({_plural(n_bodies, 'body', 'bodies')}, {_plural(len(voids), 'void')}). "
                "That is fine for multi-part plates; check that it is intended.",
                [
                    _loc(
                        _mid(s.lo, s.hi),
                        faces=len(s.faces),
                        volume_mm3=_r(s.signed_volume),
                        role=nest.role[i],
                    )
                    for i, s in enumerate(shell_list)
                ],
            )
        )
    tiny = [s for s in shell_list if s.size < opts.tiny_shell] if len(shell_list) > 1 else []
    if tiny:
        findings.append(
            Finding(
                "tiny_shells",
                WARNING,
                "Tiny floating shells",
                len(tiny),
                f"{_plural(len(tiny), 'shell')} "
                f"{_v(len(tiny), 'is', 'are')}"
                f" smaller than {opts.tiny_shell:g} mm in every "
                "direction: usually export debris that prints as specks or not at all.",
                [_loc(_mid(s.lo, s.hi), faces=len(s.faces), size_mm=_r(s.size, 3)) for s in tiny],
                [f for s in tiny for f in s.faces],
            )
        )

    # Stored normals versus normals implied by the winding.
    mismatched: list[int] = []
    missing = 0
    cos_tol = math.cos(math.radians(opts.normal_tolerance_deg))
    if mesh.source_format in ("ascii", "binary"):
        for f in geometric:
            stored = mesh.stored_normals[f]
            s_len = math.sqrt(dot(stored, stored))
            if s_len < 0.5:
                missing += 1
                continue
            if dot(normalize(stored), mesh.face_normal(f)) < cos_tol:
                mismatched.append(f)
    if mismatched:
        findings.append(
            Finding(
                "normal_mismatch",
                WARNING,
                "Stored normals disagree with winding",
                len(mismatched),
                f"{_plural(len(mismatched), 'face')} "
                f"{_v(len(mismatched), 'stores', 'store')}"
                f" a normal more than "
                f"{opts.normal_tolerance_deg:g} deg away from the one implied by the vertex "
                "order. Slicers use the vertex order; `stl-doctor repair` rewrites the normals.",
                [_loc(mesh.face_centroid(f), face=f) for f in mismatched],
                mismatched,
            )
        )
    if missing:
        findings.append(
            Finding(
                "missing_normals",
                INFO,
                "Zero stored normals",
                missing,
                f"{_plural(missing, 'face')} "
                f"{_v(missing, 'stores', 'store')}"
                f" a zero normal. This is allowed by the format "
                "and harmless for slicers.",
            )
        )

    intersections: list[tuple[int, int]] = []
    if opts.check_intersections:
        intersections = self_intersections(mesh, geometric)
        if intersections:
            inter_faces = sorted({f for pair in intersections for f in pair})
            findings.append(
                Finding(
                    "self_intersections",
                    WARNING,
                    "Self-intersecting faces",
                    len(inter_faces),
                    f"{_plural(len(inter_faces), 'face')} "
                    f"{_v(len(inter_faces), 'crosses', 'cross')}"
                    f" another face "
                    f"({_plural(len(intersections), 'intersecting pair')}). Overlapping bodies "
                    "are usually merged by the slicer; intersections within one body can "
                    "produce missing or extra walls.",
                    [
                        _loc(_mid(mesh.face_centroid(a), mesh.face_centroid(b)), faces=[a, b])
                        for a, b in intersections
                    ],
                    inter_faces,
                )
            )

    # Bed fit.
    fits, turned = opts.bed.fits(dims)
    if not fits:
        how = "fits only after turning it 90 deg about Z" if turned else "does not fit"
        findings.append(
            Finding(
                "exceeds_bed",
                WARNING if turned else ERROR,
                "Larger than the build volume",
                1,
                f"Part is {dims[0]:.2f} x {dims[1]:.2f} x {dims[2]:.2f} mm and {how} on "
                f"{opts.bed.label()}.",
                [_loc(_mid(lo, hi))],
            )
        )

    overhang = overhangs(pmesh, geometric, opts.overhang_angle)
    if overhang.faces:
        findings.append(
            Finding(
                "overhangs",
                WARNING,
                "Overhangs that need support",
                len(overhang.faces),
                f"{overhang.area:.2f} mm^2 of downward surface is steeper than "
                f"{opts.overhang_angle:g} deg from vertical (excluding the face on the bed); "
                f"estimated support footprint {overhang.projected_area:.2f} mm^2.",
                [
                    _loc(mesh.face_centroid(f), face=f, area_mm2=_r(areas[f], 3))
                    for f in sorted(overhang.faces, key=lambda f: -areas[f])
                ],
                overhang.faces,
            )
        )

    thin: ThinWallResult | None = None
    if opts.check_thin_walls:
        threshold = opts.nozzle * opts.thin_factor
        thin = thin_walls(pmesh, geometric, threshold, TriangleGrid(pmesh, geometric))
        if thin.faces:
            msg = (
                f"{_plural(len(thin.faces), 'wall face')} ({thin.area:.2f} mm^2) "
                f"{_v(len(thin.faces), 'is', 'are')}"
                f" thinner than "
                f"{threshold:g} mm ({opts.thin_factor:g} x {opts.nozzle:g} mm nozzle); thinnest "
                f"{thin.min_thickness or 0.0:.3f} mm. They may print as a single line or vanish."
            )
            if orient.conflicts:
                msg += " The surface is non-orientable, so this result is unreliable."
            findings.append(
                Finding(
                    "thin_walls",
                    WARNING,
                    "Thin walls",
                    len(thin.faces),
                    msg,
                    [
                        _loc(mesh.face_centroid(f), face=f, thickness_mm=_r(thin.thickness[f], 3))
                        for f in sorted(thin.faces, key=lambda f: thin.thickness[f])
                    ],
                    thin.faces,
                )
            )

    candidates: list[OrientationCandidate] = []
    if opts.suggest_orientation and overhang.faces and geometric:
        candidates = best_orientations(
            pmesh, geometric, opts.overhang_angle, opts.orientation_samples
        )

    volume = sum(s.signed_volume for s in shell_list)
    # Overlapping or buried bodies make the summed volume larger than what prints.
    shell_of = {f: i for i, s in enumerate(shell_list) for f in s.faces}
    overlapping = any(
        a in shell_of and b in shell_of and shell_of[a] != shell_of[b] for a, b in intersections
    )
    area = sum(areas[f] for f in active)
    watertight = not boundary and not non_manifold and not orient.inconsistent_edges
    stats: dict[str, Any] = {
        "format": mesh.source_format,
        "name": mesh.name,
        "facets_in_file": mesh.source_count,
        "faces": n_faces,
        "vertices": len(mesh.vertices),
        "exact_unique_vertices": mesh.exact_unique_vertices,
        "edges": len(edges),
        "min": _pt(lo),
        "max": _pt(hi),
        "size_mm": _pt(dims),
        "volume_mm3": _r(volume),
        "volume_is_sum_of_overlapping_shells": overlapping or bool(embedded),
        "voids": len(voids),
        "area_mm2": _r(area),
        "shells": len(shell_list),
        "watertight": watertight,
        "fits_bed": fits,
        "fits_bed_rotated": turned,
        "warnings": list(mesh.warnings),
    }
    findings.sort(key=lambda f: _RANK[f.severity])
    problem = set(degenerate) | set(duplicates) | set(flipped)
    problem |= {f for pair in intersections for f in pair}
    return Report(
        path=path,
        mesh=mesh,
        options=opts,
        stats=stats,
        findings=findings,
        shells=shell_list,
        overhang=overhang,
        thin=thin,
        orientation=candidates,
        boundary_edges=b_edges,
        non_manifold_edges=nm_edges,
        problem_faces=problem,
        shell_roles=list(nest.role),
        shell_parents=list(nest.parent),
        objects=_per_object(mesh, findings),
    )


def _per_object(mesh: Mesh, findings: list[Finding]) -> list[dict[str, Any]]:
    """Break the whole-file result down by object (3MF build item).

    Every check ran on the whole file, so problems between objects (overlaps, bed fit of the plate)
    are found as well; here each finding that points at faces is attributed to the objects owning
    those faces. Findings about the file as a whole (bed fit, several shells) are not repeated.
    """
    if len(mesh.objects) < 2:
        return []
    out: list[dict[str, Any]] = []
    for obj in mesh.objects:
        lo_f, hi_f = obj.start, obj.start + obj.count
        entry: dict[str, Any] = {"name": obj.name, "faces": obj.count}
        hits = []
        for f in findings:
            n = sum(1 for face in f.faces if lo_f <= face < hi_f)
            if n:
                hits.append({"code": f.code, "severity": f.severity, "count": n})
        pts = [mesh.vertices[i] for face in range(lo_f, hi_f) for i in mesh.faces[face]]
        if pts:
            lo = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts))
            hi = (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
            entry["min"] = _pt(lo)
            entry["max"] = _pt(hi)
            entry["size_mm"] = _pt((hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]))
        else:
            hits.append({"code": "empty_object", "severity": ERROR, "count": 1})
        entry["volume_mm3"] = _r(signed_volume(mesh, list(range(lo_f, hi_f))))
        sev = {h["severity"] for h in hits}
        entry["verdict"] = "fail" if ERROR in sev else "warn" if WARNING in sev else "pass"
        entry["findings"] = hits
        out.append(entry)
    return out
