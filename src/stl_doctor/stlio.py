"""Reading and writing STL files (ASCII and binary).

The format is detected from the content, never from the file extension:

* A file whose size is exactly ``84 + 50 * n`` bytes, where ``n`` is the little-endian facet count
  at offset 80, is binary -- even when its 80-byte header starts with ``solid`` (many exporters
  write that).
* Otherwise a file starting with ``solid`` whose first kilobytes are text and mention ``facet`` or
  ``endsolid`` is parsed as ASCII.
* Anything else of at least 84 bytes is treated as binary and checked for truncation.

Facets with NaN or infinite coordinates are dropped and recorded in ``TriangleSoup.non_finite`` so
that the checker can report them instead of crashing on them.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .vec import ZERO, Triangle, Vec3, is_finite, triangle_normal

_HEADER_SIZE = 80
_RECORD = struct.Struct("<12fH")  # normal, 3 vertices, attribute byte count = 50 bytes


class STLError(ValueError):
    """Raised when a mesh file cannot be read. The message says what is wrong and where."""


@dataclass
class SoupObject:
    """One object of a multi-object file: ``count`` triangles starting at ``start``."""

    name: str
    start: int
    count: int


@dataclass
class TriangleSoup:
    """Triangles exactly as stored in a file, before vertex welding."""

    triangles: list[Triangle]
    normals: list[Vec3]
    fmt: str
    name: str = ""
    source_count: int = 0
    non_finite: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    objects: list[SoupObject] = field(default_factory=list)


def detect_format(data: bytes) -> str:
    """Return ``"binary"`` or ``"ascii"`` for raw STL bytes, or raise :class:`STLError`."""
    if not data:
        raise STLError("file is empty")
    if len(data) >= _HEADER_SIZE + 4:
        (count,) = struct.unpack_from("<I", data, _HEADER_SIZE)
        if _HEADER_SIZE + 4 + _RECORD.size * count == len(data):
            return "binary"
    if data.lstrip()[:5].lower() == b"solid" and _looks_like_text(data):
        return "ascii"
    if len(data) >= _HEADER_SIZE + 4:
        return "binary"
    raise STLError(
        f"file is {len(data)} bytes: too short for a binary STL (needs at least 84) "
        "and does not start with 'solid'"
    )


def _looks_like_text(data: bytes) -> bool:
    head = data[:4096]
    if b"\x00" in head:
        return False
    lowered = head.lower()
    return b"facet" in lowered or b"endsolid" in lowered


def read_stl_bytes(data: bytes) -> TriangleSoup:
    fmt = detect_format(data)
    if fmt == "binary":
        return _parse_binary(data)
    return _parse_ascii(data)


def read_stl(path: str | Path) -> TriangleSoup:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise STLError(f"cannot read {path}: {exc.strerror or exc}") from exc
    return read_stl_bytes(data)


def _clean_normal(n: Vec3) -> Vec3:
    return n if is_finite(n) else ZERO


def _parse_binary(data: bytes) -> TriangleSoup:
    (count,) = struct.unpack_from("<I", data, _HEADER_SIZE)
    expected = _HEADER_SIZE + 4 + _RECORD.size * count
    warnings: list[str] = []
    if len(data) < expected:
        complete = (len(data) - _HEADER_SIZE - 4) // _RECORD.size
        raise STLError(
            f"binary STL header declares {count} facets ({expected} bytes) but the file has "
            f"{len(data)} bytes ({complete} complete facets); the file looks truncated"
        )
    if len(data) > expected:
        warnings.append(f"{len(data) - expected} trailing bytes after the last facet were ignored")
    if count == 0:
        raise STLError("binary STL declares 0 facets")
    name = data[:_HEADER_SIZE].split(b"\x00", 1)[0].decode("latin-1").strip()
    if name.lower().startswith("solid"):
        name = name[5:].strip()
    triangles: list[Triangle] = []
    normals: list[Vec3] = []
    non_finite: list[int] = []
    body = data[_HEADER_SIZE + 4 : expected]
    for index, rec in enumerate(_RECORD.iter_unpack(body)):
        a = (rec[3], rec[4], rec[5])
        b = (rec[6], rec[7], rec[8])
        c = (rec[9], rec[10], rec[11])
        if not (is_finite(a) and is_finite(b) and is_finite(c)):
            non_finite.append(index)
            continue
        triangles.append((a, b, c))
        normals.append(_clean_normal((rec[0], rec[1], rec[2])))
    if not triangles:
        raise STLError(f"all {count} facets have non-finite (NaN/inf) coordinates")
    return TriangleSoup(triangles, normals, "binary", name, count, non_finite, warnings)


def _floats(parts: list[str], lineno: int, what: str) -> Vec3:
    if len(parts) != 3:
        raise STLError(f"line {lineno}: expected 3 numbers after '{what}', got {len(parts)}")
    try:
        x, y, z = (float(p) for p in parts)
    except ValueError as exc:
        raise STLError(f"line {lineno}: invalid number in '{what}' ({exc})") from exc
    return (x, y, z)


def _parse_ascii(data: bytes) -> TriangleSoup:
    text = data.decode("latin-1")
    triangles: list[Triangle] = []
    normals: list[Vec3] = []
    non_finite: list[int] = []
    warnings: list[str] = []
    name = ""
    facet_line = 0
    normal: Vec3 = ZERO
    loop: list[Vec3] | None = None
    in_facet = False
    index = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if not parts:
            continue
        kw = parts[0].lower()
        if kw == "solid":
            if not name:
                name = " ".join(parts[1:])
        elif kw == "endsolid":
            pass
        elif kw == "facet":
            if in_facet:
                raise STLError(f"line {lineno}: 'facet' before 'endfacet' of line {facet_line}")
            in_facet = True
            facet_line = lineno
            if len(parts) >= 2 and parts[1].lower() == "normal":
                normal = _floats(parts[2:], lineno, "facet normal")
            else:
                normal = ZERO
        elif kw == "outer":
            if not in_facet:
                raise STLError(f"line {lineno}: 'outer loop' outside a facet")
            loop = []
        elif kw == "vertex":
            if loop is None:
                raise STLError(f"line {lineno}: 'vertex' outside 'outer loop'")
            loop.append(_floats(parts[1:], lineno, "vertex"))
        elif kw == "endloop":
            if loop is None:
                raise STLError(f"line {lineno}: 'endloop' without 'outer loop'")
        elif kw == "endfacet":
            if not in_facet or loop is None:
                raise STLError(f"line {lineno}: 'endfacet' without a complete facet")
            if len(loop) < 3:
                raise STLError(f"facet at line {facet_line} has {len(loop)} vertices, needs 3")
            if len(loop) > 3:
                warnings.append(
                    f"facet at line {facet_line} has {len(loop)} vertices; fan-triangulated"
                )
            for k in range(1, len(loop) - 1):
                tri = (loop[0], loop[k], loop[k + 1])
                if all(is_finite(v) for v in tri):
                    triangles.append(tri)
                    normals.append(_clean_normal(normal))
                else:
                    non_finite.append(index)
                index += 1
            in_facet = False
            loop = None
        else:
            raise STLError(f"line {lineno}: unexpected keyword {parts[0]!r}")
    if in_facet:
        raise STLError(
            f"facet starting at line {facet_line} is not closed; the file looks truncated"
        )
    if index == 0:
        raise STLError("ASCII STL contains no facets")
    if not triangles:
        raise STLError(f"all {index} facets have non-finite (NaN/inf) coordinates")
    return TriangleSoup(triangles, normals, "ascii", name, index, non_finite, warnings)


def _normals_for(triangles: list[Triangle]) -> list[Vec3]:
    return [triangle_normal(*t) for t in triangles]


def stl_bytes(triangles: list[Triangle], *, ascii: bool = False, name: str = "stl-doctor") -> bytes:
    """Serialise triangles to STL. Normals are always recomputed from the winding."""
    normals = _normals_for(triangles)
    if ascii:
        lines = [f"solid {name}"]
        for n, (a, b, c) in zip(normals, triangles, strict=True):
            lines.append(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}")
            lines.append("    outer loop")
            for v in (a, b, c):
                lines.append(f"      vertex {v[0]:.6e} {v[1]:.6e} {v[2]:.6e}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {name}")
        return ("\n".join(lines) + "\n").encode("ascii")
    header = f"binary STL written by {name}".encode("ascii")[:_HEADER_SIZE]
    out = bytearray(header.ljust(_HEADER_SIZE, b" "))
    out += struct.pack("<I", len(triangles))
    for n, (a, b, c) in zip(normals, triangles, strict=True):
        out += _RECORD.pack(*n, *a, *b, *c, 0)
    return bytes(out)


def write_stl(
    path: str | Path, triangles: list[Triangle], *, ascii: bool = False, name: str = "stl-doctor"
) -> None:
    Path(path).write_bytes(stl_bytes(triangles, ascii=ascii, name=name))
