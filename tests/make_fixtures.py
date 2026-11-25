"""Generate the STL/3MF test fixtures in ``tests/fixtures``.

Run ``python tests/make_fixtures.py`` from the repository root. The geometry is defined here by
hand, and this file has its own tiny STL writer so that the fixtures do not depend on the code they
test. Output is deterministic; ``tests/test_fixtures.py`` checks the committed files match.

Every model is written twice: ``NAME_ascii.stl`` and ``NAME_binary.stl``. Binary headers start
with ``solid`` on purpose, because real exporters do that and a naive reader then mistakes them for
ASCII.
"""

from __future__ import annotations

import io
import math
import struct
import sys
import zipfile
from pathlib import Path

V = tuple[float, float, float]
Tri = tuple[V, V, V]
Facet = tuple[V, Tri]  # (stored normal, triangle)

OUT = Path(__file__).resolve().parent / "fixtures"


def _normal(t: Tri) -> V:
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    return (n[0] / length, n[1] / length, n[2] / length) if length else (0.0, 0.0, 0.0)


def facets(tris: list[Tri]) -> list[Facet]:
    return [(_normal(t), t) for t in tris]


def quad(a: V, b: V, c: V, d: V) -> list[Tri]:
    """Two triangles of a quad given counter-clockwise as seen from outside."""
    return [(a, b, c), (a, c, d)]


def box(lo: V, hi: V) -> list[Tri]:
    """Closed box, outward winding. Order: bottom, top, front(-y), back(+y), left(-x), right(+x)."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    return (
        quad((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))
        + quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
        + quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))
        + quad((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))
        + quad((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))
        + quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))
    )


def t_shape() -> list[Tri]:
    """A 'T' extruded 10 mm along Y. Stem 10 x 20 (x 0..10, z 0..20), bar 30 x 5 (x -10..20,
    z 20..25). The two 10 x 10 mm undersides of the bar are horizontal overhangs (200 mm^2).
    Volume = 10*20*10 + 30*5*10 = 3500 mm^3.
    """
    profile = [(0, 0), (10, 0), (10, 20), (20, 20), (20, 25), (-10, 25), (-10, 20), (0, 20)]
    # Counter-clockwise in (x, z); a CCW (x, z) triangle at y = 0 faces -Y.
    cap = [
        ((0, 0), (10, 0), (10, 20)),
        ((0, 0), (10, 20), (0, 20)),
        ((-10, 25), (-10, 20), (0, 20)),
        ((-10, 25), (0, 20), (10, 20)),
        ((-10, 25), (10, 20), (20, 20)),
        ((-10, 25), (20, 20), (20, 25)),
    ]
    tris: list[Tri] = []
    for a, b, c in cap:
        tris.append(((a[0], 0.0, a[1]), (b[0], 0.0, b[1]), (c[0], 0.0, c[1])))
        tris.append(((a[0], 10.0, a[1]), (c[0], 10.0, c[1]), (b[0], 10.0, b[1])))
    for i, p in enumerate(profile):
        q = profile[(i + 1) % len(profile)]
        tris += quad((p[0], 0.0, p[1]), (p[0], 10.0, p[1]), (q[0], 10.0, q[1]), (q[0], 0.0, q[1]))
    return [tuple((float(x), float(y), float(z)) for x, y, z in t) for t in tris]  # type: ignore[misc]


def models() -> dict[str, list[Facet]]:
    cube = box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    flipped = facets(cube)
    # Flip the first top triangle's vertex order but keep its original +Z normal, the way a
    # buggy exporter would.
    n, (a, b, c) = flipped[2]
    flipped[2] = (n, (a, c, b))
    return {
        "cube": facets(cube),
        "cube_missing_face": facets(cube[:2] + cube[4:]),
        "cube_flipped_face": flipped,
        "overlapping_cubes": facets(cube + box((5.0, 3.0, 4.0), (15.0, 13.0, 14.0))),
        "thin_plate": facets(box((0.0, 0.0, 0.0), (20.0, 0.6, 15.0))),
        "overhang_t": facets(t_shape()),
        "nonmanifold_edge": facets(cube + box((10.0, 10.0, 0.0), (20.0, 20.0, 10.0))),
        # A 20 mm cube written in inches (0.787 in): the audit's wrong-units repro.
        "cube_inch": facets(box((0.0, 0.0, 0.0), (20 / 25.4, 20 / 25.4, 20 / 25.4))),
    }


def ascii_stl(name: str, fs: list[Facet]) -> bytes:
    lines = [f"solid {name}"]
    for n, tri in fs:
        lines.append(f"  facet normal {n[0]:g} {n[1]:g} {n[2]:g}")
        lines.append("    outer loop")
        lines += [f"      vertex {v[0]:g} {v[1]:g} {v[2]:g}" for v in tri]
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    return ("\n".join(lines) + "\n").encode("ascii")


def binary_stl(name: str, fs: list[Facet]) -> bytes:
    header = f"solid {name} (binary fixture for stl-doctor)".encode("ascii").ljust(80, b" ")
    out = bytearray(header[:80])
    out += struct.pack("<I", len(fs))
    for n, (a, b, c) in fs:
        out += struct.pack("<12fH", *n, *a, *b, *c, 0)
    return bytes(out)


_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"


def _mesh_xml(tris: list[Tri]) -> str:
    verts: dict[V, int] = {}
    tri_xml = []
    for t in tris:
        ids = [verts.setdefault(v, len(verts)) for v in t]
        tri_xml.append(f'<triangle v1="{ids[0]}" v2="{ids[1]}" v3="{ids[2]}"/>')
    vert_xml = "".join(f'<vertex x="{x:g}" y="{y:g}" z="{z:g}"/>' for x, y, z in verts)
    return f"<mesh><vertices>{vert_xml}</vertices><triangles>{''.join(tri_xml)}</triangles></mesh>"


def _zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text)
    return buf.getvalue()


_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
)
_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" '
    'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'
)


def threemf_simple() -> bytes:
    """Cube with the mesh directly in the root model, placed with a +5 mm Z translation."""
    model = (
        f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="{_CORE}">'
        f'<resources><object id="1" type="model" name="cube">'
        f"{_mesh_xml(box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)))}</object></resources>"
        '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 5"/></build></model>'
    )
    return _zip({"[Content_Types].xml": _TYPES, "_rels/.rels": _RELS, "3D/3dmodel.model": model})


def threemf_components() -> bytes:
    """Bambu/Prusa style: the root object only references a mesh in another part (p:path).

    The component transform scales X by 2 (cube becomes 20 x 10 x 10, volume 2000 mm^3).
    """
    root = (
        f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="{_CORE}" '
        f'xmlns:p="{_PROD}"><resources><object id="2" type="model" name="assembly"><components>'
        '<component p:path="/3D/Objects/object_1.model" objectid="1" '
        'transform="2 0 0 0 1 0 0 0 1 0 0 0"/></components></object></resources>'
        '<build><item objectid="2"/></build></model>'
    )
    part = (
        f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="{_CORE}">'
        f'<resources><object id="1" type="model">'
        f"{_mesh_xml(box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)))}</object></resources>"
        "<build/></model>"
    )
    return _zip(
        {
            "[Content_Types].xml": _TYPES,
            "_rels/.rels": _RELS,
            "3D/3dmodel.model": root,
            "3D/Objects/object_1.model": part,
        }
    )


def corrupt_files() -> dict[str, bytes]:
    cube = facets(box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)))
    full = binary_stl("cube", cube)
    nan_cube = list(cube)
    n, (a, b, c) = nan_cube[5]
    nan_cube[5] = (n, ((math.nan, a[1], a[2]), b, c))
    n, (a, b, c) = nan_cube[7]
    nan_cube[7] = (n, (a, (b[0], math.inf, b[2]), c))
    text = ascii_stl("cube", cube).decode("ascii")
    return {
        "corrupt_truncated_binary.stl": full[:-20],
        "corrupt_garbage.stl": b"this is not a mesh\n",
        "corrupt_bad_number_ascii.stl": text.replace("vertex 10 0 0", "vertex 10 0 zero", 1).encode(
            "ascii"
        ),
        "corrupt_unclosed_ascii.stl": text[: text.index("endloop", 600)].encode("ascii"),
        "nan_vertex_binary.stl": binary_stl("cube_nan", nan_cube),
    }


def generate(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    files: dict[str, bytes] = {}
    for name, fs in models().items():
        files[f"{name}_ascii.stl"] = ascii_stl(name, fs)
        files[f"{name}_binary.stl"] = binary_stl(name, fs)
    files.update(corrupt_files())
    files["cube.3mf"] = threemf_simple()
    files["cube_components.3mf"] = threemf_components()
    for fname, data in sorted(files.items()):
        path = out / fname
        path.write_bytes(data)
        written.append(path)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    for p in generate(target):
        print(p.name)
