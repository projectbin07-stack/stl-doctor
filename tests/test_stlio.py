from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from conftest import FIXTURES
from stl_doctor.stlio import STLError, detect_format, read_stl, read_stl_bytes, stl_bytes

CUBE_CORNERS = {(x, y, z) for x in (0.0, 10.0) for y in (0.0, 10.0) for z in (0.0, 10.0)}


def test_ascii_and_binary_cube_read_identically() -> None:
    a = read_stl(FIXTURES / "cube_ascii.stl")
    b = read_stl(FIXTURES / "cube_binary.stl")
    assert a.fmt == "ascii"
    assert b.fmt == "binary"
    assert a.triangles == b.triangles
    assert a.normals == b.normals
    assert len(a.triangles) == 12
    assert {v for t in a.triangles for v in t} == CUBE_CORNERS
    assert a.name == "cube"


def test_binary_with_solid_header_is_detected_by_size() -> None:
    data = (FIXTURES / "cube_binary.stl").read_bytes()
    assert data[:5] == b"solid"
    assert detect_format(data) == "binary"
    assert read_stl_bytes(data).name.startswith("cube")


def test_detects_ascii_regardless_of_extension(tmp_path: Path) -> None:
    p = tmp_path / "mesh.bin"
    p.write_bytes((FIXTURES / "cube_ascii.stl").read_bytes())
    assert read_stl(p).fmt == "ascii"


def test_truncated_binary_reports_counts() -> None:
    with pytest.raises(STLError, match=r"declares 12 facets .* 664 bytes \(11 complete facets\)"):
        read_stl(FIXTURES / "corrupt_truncated_binary.stl")


def test_garbage_is_rejected() -> None:
    with pytest.raises(STLError, match="too short"):
        read_stl(FIXTURES / "corrupt_garbage.stl")


def test_bad_number_reports_line() -> None:
    with pytest.raises(STLError, match=r"line 13: invalid number in 'vertex'"):
        read_stl(FIXTURES / "corrupt_bad_number_ascii.stl")


def test_unclosed_ascii_facet() -> None:
    with pytest.raises(STLError, match="not closed"):
        read_stl(FIXTURES / "corrupt_unclosed_ascii.stl")


def test_non_finite_facets_are_dropped_and_recorded() -> None:
    soup = read_stl(FIXTURES / "nan_vertex_binary.stl")
    assert soup.non_finite == [5, 7]
    assert len(soup.triangles) == 10
    assert soup.source_count == 12
    assert all(math.isfinite(c) for t in soup.triangles for v in t for c in v)


def test_empty_file() -> None:
    with pytest.raises(STLError, match="empty"):
        read_stl_bytes(b"")


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(STLError, match="cannot read"):
        read_stl(tmp_path / "nope.stl")


def test_zero_facet_binary() -> None:
    data = b"x" * 80 + struct.pack("<I", 0)
    with pytest.raises(STLError, match="0 facets"):
        read_stl_bytes(data)


def test_trailing_bytes_warn() -> None:
    data = (FIXTURES / "cube_binary.stl").read_bytes() + b"\x00\x00\x00"
    soup = read_stl_bytes(data)
    assert len(soup.triangles) == 12
    assert soup.warnings == ["3 trailing bytes after the last facet were ignored"]


def test_all_non_finite_binary() -> None:
    rec = struct.pack("<12fH", 0, 0, 1, math.nan, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    with pytest.raises(STLError, match="non-finite"):
        read_stl_bytes(b"h" * 80 + struct.pack("<I", 1) + rec)


def _ascii(body: str) -> bytes:
    return f"solid t\n{body}\nendsolid t\n".encode()


def test_ascii_quad_is_fan_triangulated() -> None:
    body = (
        "facet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 1 1 0\n"
        "vertex 0 1 0\nendloop\nendfacet"
    )
    soup = read_stl_bytes(_ascii(body))
    assert soup.triangles == [
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
        ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
    ]
    assert "fan-triangulated" in soup.warnings[0]


def test_ascii_without_normal_and_nan_normal() -> None:
    body = (
        "facet\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\n"
        "facet normal nan 0 0\nouter loop\nvertex 0 0 1\nvertex 1 0 1\nvertex 0 1 1\nendloop\n"
        "endfacet"
    )
    soup = read_stl_bytes(_ascii(body))
    assert soup.normals == [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("vertex 0 0 0", "outside 'outer loop'"),
        ("outer loop", "outside a facet"),
        ("facet normal 0 0 1\nfacet normal 0 0 1", "before 'endfacet'"),
        ("facet normal 0 0 1\nendfacet", "without a complete facet"),
        ("endloop", "without 'outer loop'"),
        ("facet normal 0 0\nendfacet", "expected 3 numbers"),
        ("wibble", "unexpected keyword"),
        ("facet normal 0 0 1\nouter loop\nvertex 0 0 0\nendloop\nendfacet", "has 1 vertices"),
        ("", "no facets"),
        (
            "facet normal 0 0 1\nouter loop\nvertex inf 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
            "endloop\nendfacet",
            "non-finite",
        ),
    ],
)
def test_ascii_errors(body: str, message: str) -> None:
    with pytest.raises(STLError, match=message):
        read_stl_bytes(_ascii(body))


@pytest.mark.parametrize("ascii", [True, False])
def test_writer_round_trip(ascii: bool) -> None:
    tris = read_stl(FIXTURES / "overhang_t_binary.stl").triangles
    soup = read_stl_bytes(stl_bytes(tris, ascii=ascii, name="t"))
    assert soup.fmt == ("ascii" if ascii else "binary")
    assert len(soup.triangles) == len(tris)
    for got, want in zip(soup.triangles, tris, strict=True):
        for gv, wv in zip(got, want, strict=True):
            assert gv == pytest.approx(wv, abs=1e-5)
    # Written normals are recomputed from the winding: the first cap triangle faces -Y.
    assert soup.normals[0] == pytest.approx((0.0, -1.0, 0.0))
