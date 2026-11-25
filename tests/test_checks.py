"""Expected counts for every generated fixture, derived by hand from the geometry in
``make_fixtures.py`` (comments give the derivation)."""

from __future__ import annotations

import pytest

import make_fixtures
from conftest import check
from stl_doctor.analysis import Options, analyze
from stl_doctor.mesh import Mesh
from stl_doctor.printers import PRINTERS, Bed
from stl_doctor.stlio import TriangleSoup
from stl_doctor.vec import Triangle, Vec3


def _box(lo: Vec3, hi: Vec3) -> list[Triangle]:
    return list(make_fixtures.box(lo, hi))


def test_cube_passes(fmt: str) -> None:
    r = check(f"cube_{fmt}.stl")
    assert r.verdict == "pass"
    assert r.findings == []
    s = r.stats
    assert (s["faces"], s["vertices"], s["edges"], s["shells"]) == (12, 8, 18, 1)
    assert s["volume_mm3"] == pytest.approx(1000.0)  # 10^3
    assert s["area_mm2"] == pytest.approx(600.0)  # 6 * 10^2
    assert s["watertight"] is True
    assert s["size_mm"] == [10.0, 10.0, 10.0]
    assert r.exit_code() == 0


def test_cube_missing_face(fmt: str) -> None:
    r = check(f"cube_missing_face_{fmt}.stl")
    assert r.verdict == "fail"
    holes = r.finding("open_boundaries")
    assert holes is not None
    assert holes.count == 1  # one square hole
    assert len(r.boundary_edges) == 4
    loc = holes.locations[0]
    assert loc["perimeter_mm"] == pytest.approx(40.0)  # 4 * 10
    assert (loc["x"], loc["y"], loc["z"]) == (5.0, 5.0, 10.0)
    # Without the top (which contributes A*z/3 = 100*10/3) the signed volume is 1000 - 333.33.
    assert r.stats["volume_mm3"] == pytest.approx(2000.0 / 3.0, abs=1e-3)
    assert r.stats["watertight"] is False
    assert r.exit_code() == 2


def test_cube_flipped_face(fmt: str) -> None:
    r = check(f"cube_flipped_face_{fmt}.stl")
    wind = r.finding("inconsistent_winding")
    assert wind is not None
    assert wind.count == 1
    assert wind.faces == [2]
    assert len(wind.edges) == 3  # all three edges of the flipped triangle
    normals = r.finding("normal_mismatch")
    assert normals is not None
    assert normals.faces == [2]
    # A top triangle contributes +50*10/3; flipped it contributes the negative: 1000 - 2*166.67.
    assert r.stats["volume_mm3"] == pytest.approx(2000.0 / 3.0, abs=1e-3)
    # Printability uses corrected winding, so the flipped top face is not a fake overhang.
    assert r.finding("overhangs") is None
    assert r.verdict == "fail"


def test_overlapping_cubes(fmt: str) -> None:
    r = check(f"overlapping_cubes_{fmt}.stl")
    inter = r.finding("self_intersections")
    assert inter is not None
    # Cube A = [0,10]^3, cube B = [5,15]x[3,13]x[4,14]. A's +X/+Y/+Z faces cross B's -X/-Y/-Z
    # faces along L-shaped paths that pass through both triangles of each face whichever
    # diagonal splits it, so exactly 6 + 6 triangles are crossed.
    assert inter.count == 12
    mesh = r.mesh
    for f in inter.faces:
        n = mesh.face_normal(f)
        if f < 12:
            assert max(n) == 1.0  # cube A: an outward +axis face
        else:
            assert min(n) == -1.0  # cube B: an outward -axis face
    assert r.stats["shells"] == 2
    assert r.stats["volume_mm3"] == pytest.approx(2000.0)  # overlap counted twice
    # B's bottom (z = 4, 10 x 10) floats above the bed: 100 mm^2 overhang.
    assert r.finding("overhangs") is not None
    assert r.overhang.area == pytest.approx(100.0)
    assert r.verdict == "warn"
    assert r.exit_code() == 0
    assert r.exit_code(strict=True) == 1


def test_thin_plate(fmt: str) -> None:
    r = check(f"thin_plate_{fmt}.stl")
    thin = r.finding("thin_walls")
    assert thin is not None
    assert thin.count == 4  # the two 20 x 15 faces, two triangles each
    assert r.thin is not None
    assert r.thin.area == pytest.approx(600.0)  # 2 * 20 * 15
    assert r.thin.min_thickness == pytest.approx(0.6, abs=1e-6)
    assert r.stats["volume_mm3"] == pytest.approx(180.0)  # 20 * 0.6 * 15
    # 0.6 mm passes once the limit is 1 x 0.4 mm nozzle.
    assert check(f"thin_plate_{fmt}.stl", thin_factor=1.0).finding("thin_walls") is None
    assert check(f"thin_plate_{fmt}.stl", check_thin_walls=False).thin is None


def test_overhang_t(fmt: str) -> None:
    r = check(f"overhang_t_{fmt}.stl")
    assert r.stats["volume_mm3"] == pytest.approx(3500.0)  # 10*20*10 + 30*5*10
    # 2 caps of 350 + perimeter (10+20+10+5+30+5+10+20 = 110) * 10 deep.
    assert r.stats["area_mm2"] == pytest.approx(1800.0)
    over = r.finding("overhangs")
    assert over is not None
    assert over.count == 4
    assert r.overhang.area == pytest.approx(200.0)  # two 10 x 10 undersides
    assert r.overhang.projected_area == pytest.approx(200.0)
    assert r.orientation, "orientation suggested when there are overhangs"
    best = r.orientation[0]
    assert best.overhang_area == pytest.approx(0.0)
    assert best.contact_area == pytest.approx(350.0)  # lying on its T-shaped side
    assert r.verdict == "warn"


def test_nonmanifold_edge(fmt: str) -> None:
    r = check(f"nonmanifold_edge_{fmt}.stl")
    nm = r.finding("non_manifold_edges")
    assert nm is not None
    assert nm.count == 1
    assert nm.locations[0] == {"x": 10.0, "y": 10.0, "z": 5.0, "faces": 4}
    assert r.stats["shells"] == 2  # non-manifold edges do not join shells
    assert [s.signed_volume for s in r.shells] == pytest.approx([1000.0, 1000.0])
    assert r.verdict == "fail"


def test_nan_vertices() -> None:
    r = check("nan_vertex_binary.stl")
    bad = r.finding("non_finite_vertices")
    assert bad is not None
    assert bad.count == 2
    assert bad.locations == [{"facet": 5}, {"facet": 7}]
    holes = r.finding("open_boundaries")
    assert holes is not None
    assert holes.count == 2


def test_tiny_floating_shell() -> None:
    tris = _box((0, 0, 0), (10, 10, 10)) + _box((20, 20, 0), (20.5, 20.5, 0.5))
    r = analyze(Mesh.from_triangles(tris))
    tiny = r.finding("tiny_shells")
    assert tiny is not None
    assert tiny.count == 1
    assert tiny.locations[0]["size_mm"] == 0.5
    multi = r.finding("multiple_shells")
    assert multi is not None
    assert multi.count == 2


def test_inside_out_cube() -> None:
    tris = [(a, c, b) for a, b, c in _box((0, 0, 0), (10, 10, 10))]
    r = analyze(Mesh.from_triangles(tris))
    inv = r.finding("inside_out")
    assert inv is not None
    assert inv.count == 1
    assert r.stats["volume_mm3"] == pytest.approx(-1000.0)
    # Overhangs are computed on corrected winding: an inverted cube still has none.
    assert r.finding("overhangs") is None
    assert r.finding("inconsistent_winding") is None


def test_degenerate_and_duplicate_faces() -> None:
    cube = _box((0, 0, 0), (10, 10, 10))
    collapsed: Triangle = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    tris = [*cube, collapsed, cube[0]]
    r = analyze(Mesh.from_triangles(tris))
    deg = r.finding("degenerate_faces")
    dup = r.finding("duplicate_faces")
    assert deg is not None
    assert deg.faces == [12]
    assert dup is not None
    assert dup.faces == [13]
    assert dup.locations[0]["duplicate_of"] == 0
    # Excluded from topology, so the cube is still watertight.
    assert r.stats["watertight"] is True
    assert r.finding("non_manifold_edges") is None
    assert r.verdict == "warn"


def test_near_duplicate_vertices_welded() -> None:
    cube = _box((0, 0, 0), (10, 10, 10))
    a, b, c = cube[0]
    cube[0] = ((a[0] + 5e-5, a[1], a[2]), b, c)
    assert analyze(Mesh.from_triangles(cube, tolerance=1e-4)).stats["watertight"] is True
    loose = analyze(Mesh.from_triangles(cube, tolerance=0.0))
    assert loose.stats["watertight"] is False
    assert loose.finding("open_boundaries") is not None


def test_bed_fit() -> None:
    big = _box((0, 0, 0), (200, 100, 50))
    r = analyze(Mesh.from_triangles(big), Options(bed=PRINTERS["a1-mini"]))
    f = r.finding("exceeds_bed")
    assert f is not None
    assert f.severity == "error"
    assert r.stats["fits_bed"] is False
    assert analyze(Mesh.from_triangles(big), Options(bed=PRINTERS["p1s"])).verdict == "pass"
    # 100 x 200 fits a 220 x 120 bed only after turning it.
    turned = analyze(
        Mesh.from_triangles(_box((0, 0, 0), (100, 200, 50))),
        Options(bed=Bed("custom", 220, 120, 100)),
    )
    tf = turned.finding("exceeds_bed")
    assert tf is not None
    assert tf.severity == "warning"
    assert turned.stats["fits_bed_rotated"] is True


def test_missing_normals_info() -> None:
    tris = _box((0, 0, 0), (10, 10, 10))
    soup = TriangleSoup(tris, [(0.0, 0.0, 0.0)] * 12, "binary", source_count=12)
    r = analyze(Mesh.from_soup(soup))
    f = r.finding("missing_normals")
    assert f is not None
    assert f.count == 12
    assert f.severity == "info"
    assert r.verdict == "pass"


def test_intersections_can_be_skipped() -> None:
    r = check("overlapping_cubes_binary.stl", check_intersections=False)
    assert r.finding("self_intersections") is None


def test_json_dict_shape() -> None:
    r = check("overhang_t_binary.stl")
    d = r.to_dict()
    assert d["verdict"] == "warn"
    assert d["counts"] == {"error": 0, "warning": 1, "info": 0}
    assert d["orientation"]["heuristic"] is True
    assert d["orientation"]["overhang_mm2"] == 0.0
    assert d["options"]["thin_wall_threshold_mm"] == pytest.approx(0.8)
    assert d["findings"][0]["code"] == "overhangs"
    assert d["shells"][0]["closed"] is True
    small = check("overlapping_cubes_binary.stl", max_locations=2).to_dict()
    inter = next(f for f in small["findings"] if f["code"] == "self_intersections")
    assert len(inter["locations"]) == 2
    assert inter["locations_truncated"] is True
    assert {f["code"] for f in small["findings"]} == {
        "self_intersections",
        "overhangs",
        "multiple_shells",
    }


def test_inch_scale_cube_gets_a_units_hint(fmt: str) -> None:
    # The audit repro: a 20 mm cube exported in inches arrives as a 0.787 mm cube.
    r = check(f"cube_inch_{fmt}.stl")
    units = r.finding("units")
    assert units is not None
    assert units.severity == "warning"
    assert units.title == "Implausibly small: wrong units?"
    assert "Largest dimension is 0.7874 mm" in units.message
    assert "x25.4 (inches): 20 x 20 x 20 mm, fits the bed" in units.message
    assert "x1000 (metres): 787.4 x 787.4 x 787.4 mm, too big for the bed" in units.message
    assert r.verdict == "warn"
    assert r.exit_code() == 0
    assert r.exit_code(strict=True) == 1


def test_metre_scale_cube_points_at_x1000() -> None:
    side = 0.02  # 20 mm written in metres
    r = analyze(Mesh.from_triangles(_box((0, 0, 0), (side, side, side))))
    units = r.finding("units")
    assert units is not None
    assert "x25.4 (inches): 0.508 x 0.508 x 0.508 mm, still tiny" in units.message
    assert "x1000 (metres): 20 x 20 x 20 mm, fits the bed" in units.message


def test_implausibly_large_model() -> None:
    # A 100 mm cube scaled from inches twice: 2540 mm.
    r = analyze(Mesh.from_triangles(_box((0, 0, 0), (2540, 2540, 2540))))
    units = r.finding("units")
    assert units is not None
    assert units.title == "Implausibly large: wrong units?"
    assert "/25.4 (scaled from inches twice): 100 x 100 x 100 mm, fits the bed" in units.message
    assert "/1000 (micrometres): 2.54 x 2.54 x 2.54 mm, fits the bed" in units.message
    assert r.finding("exceeds_bed") is not None


def test_plausible_sizes_have_no_units_hint() -> None:
    for side in (2.0, 20.0, 1000.0):
        r = analyze(Mesh.from_triangles(_box((0, 0, 0), (side, side, side))))
        assert r.finding("units") is None, side
    # Only the largest dimension counts: a 0.6 mm thick plate is thin, not mis-scaled.
    assert check("thin_plate_binary.stl").finding("units") is None


def test_units_hint_uses_the_selected_bed() -> None:
    # 0.2 mm read as metres is 200 mm: fits a P1S (256 mm), not an A1 mini (180 mm).
    tri = _box((0, 0, 0), (0.2, 0.2, 0.2))
    p1s = analyze(Mesh.from_triangles(tri)).finding("units")
    mini = analyze(Mesh.from_triangles(tri), Options(bed=PRINTERS["a1-mini"])).finding("units")
    assert p1s is not None
    assert mini is not None
    assert "x1000 (metres): 200 x 200 x 200 mm, fits the bed" in p1s.message
    assert "x1000 (metres): 200 x 200 x 200 mm, too big for the bed" in mini.message
