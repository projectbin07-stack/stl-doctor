"""Minimal 3MF mesh reader (standard library only).

Reads every build item of a 3MF package (or, when the package has no build section, every object
that no component references), following ``<components>`` references -- including
Production-extension ``p:path`` references into other model parts, which is how Bambu Studio and
PrusaSlicer store objects -- and applying item and component transforms. Each build item becomes
one entry of ``TriangleSoup.objects`` so the checker can report per object. Materials, colours and
the slicer specific metadata are ignored.
"""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .stlio import SoupObject, STLError, TriangleSoup
from .vec import ZERO, Triangle, Vec3, is_finite

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_MAX_PART_BYTES = 512 * 1024 * 1024
_MAX_DEPTH = 16

Matrix = tuple[float, ...]  # 12 values, 3MF row-vector convention
IDENTITY: Matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


def _q(tag: str) -> str:
    return f"{{{CORE_NS}}}{tag}"


def parse_transform(text: str | None) -> Matrix:
    if not text:
        return IDENTITY
    parts = text.split()
    if len(parts) != 12:
        raise STLError(f"3MF transform needs 12 numbers, got {len(parts)}: {text!r}")
    try:
        return tuple(float(p) for p in parts)
    except ValueError as exc:
        raise STLError(f"invalid 3MF transform {text!r}") from exc


def compose(first: Matrix, then: Matrix) -> Matrix:
    """Matrix applying ``first`` and then ``then`` (both in 3MF row-vector form)."""
    a = [
        [first[0], first[1], first[2]],
        [first[3], first[4], first[5]],
        [first[6], first[7], first[8]],
    ]
    b = [[then[0], then[1], then[2]], [then[3], then[4], then[5]], [then[6], then[7], then[8]]]
    m = [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    t = apply((first[9], first[10], first[11]), then)
    return (*m[0], *m[1], *m[2], *t)


def apply(p: Vec3, m: Matrix) -> Vec3:
    x, y, z = p
    return (
        x * m[0] + y * m[3] + z * m[6] + m[9],
        x * m[1] + y * m[4] + z * m[7] + m[10],
        x * m[2] + y * m[5] + z * m[8] + m[11],
    )


class _Package:
    def __init__(self, zf: zipfile.ZipFile) -> None:
        self.zf = zf
        self.cache: dict[str, ET.Element] = {}

    def model(self, part: str) -> ET.Element:
        part = part.lstrip("/")
        if part not in self.cache:
            try:
                info = self.zf.getinfo(part)
            except KeyError as exc:
                raise STLError(f"3MF package has no part {part!r}") from exc
            if info.file_size > _MAX_PART_BYTES:
                raise STLError(f"3MF part {part!r} is too large ({info.file_size} bytes)")
            try:
                self.cache[part] = ET.fromstring(self.zf.read(info))
            except ET.ParseError as exc:
                raise STLError(f"3MF part {part!r} is not valid XML: {exc}") from exc
        return self.cache[part]

    def root_part(self) -> str:
        try:
            rels = ET.fromstring(self.zf.read("_rels/.rels"))
        except (KeyError, ET.ParseError):
            rels = None
        if rels is not None:
            for rel in rels.iter(f"{{{_REL_NS}}}Relationship"):
                if rel.get("Type") == _MODEL_REL and rel.get("Target"):
                    return str(rel.get("Target")).lstrip("/")
        for name in self.zf.namelist():
            if name.lower().endswith(".model"):
                return name
        raise STLError("3MF package contains no .model part")


def _find_object(model: ET.Element, object_id: str, part: str) -> ET.Element:
    resources = model.find(_q("resources"))
    if resources is not None:
        for obj in resources.findall(_q("object")):
            if obj.get("id") == object_id:
                return obj
    raise STLError(f"3MF object id {object_id} not found in {part!r}")


def _collect(
    pkg: _Package,
    part: str,
    obj: ET.Element,
    matrix: Matrix,
    out: list[Triangle],
    depth: int,
) -> None:
    if depth > _MAX_DEPTH:
        raise STLError("3MF component references nest too deeply (cycle?)")
    mesh = obj.find(_q("mesh"))
    if mesh is not None:
        verts_el = mesh.find(_q("vertices"))
        tris_el = mesh.find(_q("triangles"))
        if verts_el is None or tris_el is None:
            raise STLError(f"3MF object {obj.get('id')} has a mesh without vertices/triangles")
        try:
            verts = [
                apply((float(v.get("x", "")), float(v.get("y", "")), float(v.get("z", ""))), matrix)
                for v in verts_el.findall(_q("vertex"))
            ]
            for t in tris_el.findall(_q("triangle")):
                i, j, k = int(t.get("v1", "")), int(t.get("v2", "")), int(t.get("v3", ""))
                out.append((verts[i], verts[j], verts[k]))
        except (ValueError, IndexError) as exc:
            raise STLError(f"3MF object {obj.get('id')}: bad vertex or triangle ({exc})") from exc
        return
    comps = obj.find(_q("components"))
    if comps is None:
        raise STLError(f"3MF object {obj.get('id')} has neither a mesh nor components")
    for comp in comps.findall(_q("component")):
        sub_part = comp.get(f"{{{PROD_NS}}}path")
        target_part = posixpath.normpath(sub_part.lstrip("/")) if sub_part else part
        sub_model = pkg.model(target_part)
        sub_obj = _find_object(sub_model, comp.get("objectid", ""), target_part)
        sub_matrix = compose(parse_transform(comp.get("transform")), matrix)
        _collect(pkg, target_part, sub_obj, sub_matrix, out, depth + 1)


def _placements(model: ET.Element, part: str) -> list[tuple[ET.Element, Matrix]]:
    """The objects to print with their transforms: every build item, in file order."""
    build = model.find(_q("build"))
    items = build.findall(_q("item")) if build is not None else []
    if items:
        return [
            (
                _find_object(model, item.get("objectid", ""), part),
                parse_transform(item.get("transform")),
            )
            for item in items
        ]
    resources = model.find(_q("resources"))
    objects = resources.findall(_q("object")) if resources is not None else []
    if not objects:
        raise STLError("3MF model has no objects")
    used = {
        c.get("objectid")
        for obj in objects
        for c in obj.iter(_q("component"))
        if not c.get(f"{{{PROD_NS}}}path")
    }
    roots = [obj for obj in objects if obj.get("id") not in used] or objects[:1]
    return [(obj, IDENTITY) for obj in roots]


def read_3mf(path: str | Path) -> TriangleSoup:
    """Read every build item of a 3MF file as one triangle soup (normals are left empty)."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise STLError(f"cannot open {path} as a 3MF (zip) package: {exc}") from exc
    good: list[Triangle] = []
    bad: list[int] = []
    objects: list[SoupObject] = []
    total = 0
    with zf:
        pkg = _Package(zf)
        part = pkg.root_part()
        model = pkg.model(part)
        placements = _placements(model, part)
        for number, (obj, matrix) in enumerate(placements, start=1):
            triangles: list[Triangle] = []
            _collect(pkg, part, obj, matrix, triangles, 0)
            start = len(good)
            for tri in triangles:
                if all(is_finite(v) for v in tri):
                    good.append(tri)
                else:
                    bad.append(total)
                total += 1
            name = obj.get("name") or f"object {obj.get('id', '?')}"
            objects.append(SoupObject(f"{number}: {name}", start, len(good) - start))
    if not good:
        raise STLError("3MF object contains no usable triangles")
    first_name = placements[0][0].get("name", "")
    name = first_name if len(placements) == 1 else f"{len(placements)} build items"
    return TriangleSoup(good, [ZERO] * len(good), "3mf", name, total, bad, [], objects)
