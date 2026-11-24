from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import make_fixtures
from conftest import FIXTURES, check
from stl_doctor.analysis import Options, analyze
from stl_doctor.cli import main
from stl_doctor.mesh import Mesh
from stl_doctor.stlio import STLError
from stl_doctor.threemf import IDENTITY, apply, compose, parse_transform, read_3mf


def test_simple_3mf_applies_build_transform() -> None:
    soup = read_3mf(FIXTURES / "cube.3mf")
    assert soup.fmt == "3mf"
    assert soup.name == "cube"
    assert len(soup.triangles) == 12
    zs = {v[2] for t in soup.triangles for v in t}
    assert zs == {5.0, 15.0}


def test_component_in_other_part_with_scale() -> None:
    report = check("cube_components.3mf")
    assert report.verdict == "pass"
    assert report.stats["size_mm"] == [20.0, 10.0, 10.0]
    assert report.stats["volume_mm3"] == pytest.approx(2000.0)


def test_compose_applies_first_then_second() -> None:
    scale_x2 = parse_transform("2 0 0 0 1 0 0 0 1 0 0 0")
    shift_z = parse_transform("1 0 0 0 1 0 0 0 1 0 0 3")
    m = compose(scale_x2, shift_z)
    assert apply((1.0, 1.0, 1.0), m) == (2.0, 1.0, 4.0)
    m2 = compose(shift_z, scale_x2)
    assert apply((1.0, 1.0, 1.0), m2) == (2.0, 1.0, 4.0)
    shift_x = parse_transform("1 0 0 0 1 0 0 0 1 1 0 0")
    assert apply((0.0, 0.0, 0.0), compose(shift_x, scale_x2)) == (2.0, 0.0, 0.0)
    assert apply((0.0, 0.0, 0.0), compose(scale_x2, shift_x)) == (1.0, 0.0, 0.0)
    assert parse_transform(None) == IDENTITY


@pytest.mark.parametrize("text", ["1 2 3", "a b c d e f g h i j k l"])
def test_bad_transform(text: str) -> None:
    with pytest.raises(STLError):
        parse_transform(text)


def _pkg(tmp_path: Path, parts: dict[str, str]) -> Path:
    p = tmp_path / "x.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        for name, text in parts.items():
            zf.writestr(name, text)
    return p


NS = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'


def test_not_a_zip(tmp_path: Path) -> None:
    p = tmp_path / "x.3mf"
    p.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(STLError, match="3MF"):
        read_3mf(p)


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        ({"readme.txt": "hi"}, "no .model part"),
        ({"3D/3dmodel.model": "<model"}, "not valid XML"),
        ({"3D/3dmodel.model": f"<model {NS}><resources/></model>"}, "no objects"),
        (
            {
                "3D/3dmodel.model": f'<model {NS}><resources/><build><item objectid="7"/></build>'
                "</model>"
            },
            "id 7 not found",
        ),
        (
            {"3D/3dmodel.model": f'<model {NS}><resources><object id="1"/></resources></model>'},
            "neither a mesh nor components",
        ),
        (
            {
                "3D/3dmodel.model": f'<model {NS}><resources><object id="1"><mesh><vertices/>'
                "</mesh></object></resources></model>"
            },
            "without vertices/triangles",
        ),
        (
            {
                "3D/3dmodel.model": f'<model {NS}><resources><object id="1"><mesh><vertices>'
                '<vertex x="0" y="0" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/>'
                "</triangles></mesh></object></resources></model>"
            },
            "bad vertex or triangle",
        ),
        (
            {
                "3D/3dmodel.model": f'<model {NS}><resources><object id="1"><components>'
                '<component objectid="1"/></components></object></resources></model>'
            },
            "nest too deeply",
        ),
        (
            {
                "3D/3dmodel.model": f'<model {NS} xmlns:p="http://schemas.microsoft.com/'
                '3dmanufacturing/production/2015/06"><resources><object id="1"><components>'
                '<component p:path="/3D/missing.model" objectid="1"/></components></object>'
                "</resources></model>"
            },
            "no part",
        ),
        (
            {
                "3D/3dmodel.model": f'<model {NS}><resources><object id="1"><mesh><vertices>'
                '<vertex x="nan" y="0" z="0"/><vertex x="1" y="0" z="0"/>'
                '<vertex x="0" y="1" z="0"/></vertices><triangles>'
                '<triangle v1="0" v2="1" v3="2"/></triangles></mesh></object></resources></model>'
            },
            "no usable triangles",
        ),
    ],
)
def test_malformed_packages(tmp_path: Path, parts: dict[str, str], message: str) -> None:
    with pytest.raises(STLError, match=message):
        read_3mf(_pkg(tmp_path, parts))


def _plate(tmp_path: Path, build: str) -> Path:
    good = make_fixtures.box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    broken = good[:-1]  # one triangle of the top missing: an open box
    model = (
        f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" {NS}><resources>'
        f'<object id="1" type="model" name="bead">{make_fixtures._mesh_xml(good)}</object>'
        f'<object id="2" type="model" name="cracked">{make_fixtures._mesh_xml(broken)}</object>'
        f"</resources>{build}</model>"
    )
    return _pkg(tmp_path, {"3D/3dmodel.model": model})


def test_every_build_item_is_read_with_its_transform(tmp_path: Path) -> None:
    path = _plate(
        tmp_path,
        '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
        '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 20 0 0"/>'
        '<item objectid="2" transform="1 0 0 0 1 0 0 0 1 40 0 0"/></build>',
    )
    soup = read_3mf(path)
    assert len(soup.triangles) == 12 + 12 + 11
    assert soup.source_count == 35
    assert [(o.name, o.start, o.count) for o in soup.objects] == [
        ("1: bead", 0, 12),
        ("2: bead", 12, 12),
        ("3: cracked", 24, 11),
    ]
    xs = {v[0] for t in soup.triangles[12:24] for v in t}
    assert xs == {20.0, 30.0}
    report = analyze(Mesh.from_soup(soup), Options(check_thin_walls=False))
    assert report.stats["size_mm"] == [50.0, 10.0, 10.0]
    assert report.verdict == "fail"  # the cracked object is open
    assert [o["verdict"] for o in report.objects] == ["pass", "pass", "fail"]
    assert report.objects[1]["min"] == [20.0, 0.0, 0.0]
    assert report.objects[0]["volume_mm3"] == pytest.approx(1000.0)
    codes = {f["code"] for f in report.objects[2]["findings"]}
    assert "open_boundaries" in codes
    assert report.to_dict()["objects"][2]["name"] == "3: cracked"


def test_check_cli_lists_failing_objects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _plate(
        tmp_path,
        '<build><item objectid="1"/>'
        '<item objectid="2" transform="1 0 0 0 1 0 0 0 1 20 0 0"/></build>',
    )
    assert main(["check", str(path), "--no-thin-walls"]) == 2
    out = capsys.readouterr().out
    assert "objects    2 build items: 1 pass, 1 fail" in out
    assert "FAIL  2: cracked: open_boundaries" in out


def test_single_item_keeps_object_name_and_no_breakdown(tmp_path: Path) -> None:
    soup = read_3mf(_plate(tmp_path, '<build><item objectid="1"/></build>'))
    assert soup.name == "bead"
    report = analyze(Mesh.from_soup(soup), Options(check_thin_walls=False))
    assert report.objects == []


def test_without_build_section_every_root_object_is_read(tmp_path: Path) -> None:
    soup = read_3mf(_plate(tmp_path, ""))
    assert [o.name for o in soup.objects] == ["1: bead", "2: cracked"]
    assert len(soup.triangles) == 23
