"""The vendored registry loads, and stl-doctor's beds are exactly its build volumes."""

from __future__ import annotations

from stl_doctor.printers import PRINTERS
from stl_doctor.registry import load_registry


def test_beds_come_from_the_registry() -> None:
    reg = load_registry()
    assert sorted(PRINTERS) == sorted(p.key for p in reg.printers)
    for spec in reg.printers:
        bed = PRINTERS[spec.key]
        assert (bed.x, bed.y, bed.z) == spec.build_volume_mm
        assert bed.name == spec.full_name
        assert spec.sources


def test_registry_lookup_matches_planner_and_slicer_names() -> None:
    reg = load_registry()
    for name, key in [("X1C", "x1c"), ("Bambu Lab X1 Carbon", "x1c"), ("A1 mini", "a1-mini")]:
        spec = reg.printer(name)
        assert spec is not None
        assert spec.key == key
