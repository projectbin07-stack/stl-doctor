"""Shared printer and material registry (stdlib only).

This module and ``print_registry.json`` next to it are vendored byte for byte into
gcode-insight (the canonical copy), printfarm-planner, print-forge and stl-doctor. CI in each
consumer compares its copies with the pinned gcode-insight release, so edit the canonical copy,
release it, then copy both files unchanged and move the pins. Nothing in here may import from
the package that contains it.

Printers are looked up by key (``a1-mini``), model (``A1 mini``), alias (``X1C``) or the
slicer's full name (``Bambu Lab X1 Carbon``), ignoring case, hyphens and underscores.
Materials resolve to their base polymer: first through the aliases in the data file
(``PLA Silk`` -> ``PLA``), then by the material's leading letters (``PETG-CF`` -> ``PETG``),
which is the rule gcode-insight 0.1.0 introduced for its density table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "REGISTRY_PATH",
    "MaterialSpec",
    "PrinterSpec",
    "Registry",
    "RegistryError",
    "Source",
    "load_registry",
    "normalise_material_name",
    "normalise_printer_name",
    "parse_registry",
]

REGISTRY_PATH = Path(__file__).with_name("print_registry.json")
#: Newest ``schema_version`` of the data file this code understands.
SCHEMA_VERSION = 1

_SEPARATORS = re.compile(r"[\s_\-]+")
_LEADING_LETTERS = re.compile(r"[A-Z]+")
_VENDOR_PREFIXES = ("BAMBU LAB ", "BAMBULAB ", "BAMBU ")


class RegistryError(ValueError):
    """The registry data file is missing, malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class Source:
    url: str
    retrieved: str
    covers: str


@dataclass(frozen=True, slots=True)
class PrinterSpec:
    """One printer model as its manufacturer specifies it."""

    key: str
    vendor: str
    model: str
    aliases: tuple[str, ...]
    build_volume_mm: tuple[float, float, float]
    enclosed: bool
    nozzle_material: str
    nozzle_included_mm: float
    nozzle_options_mm: tuple[float, ...]
    #: ``None`` when the source does not name the plate shipped with the printer.
    plates_included: tuple[str, ...] | None
    plates_compatible: tuple[str, ...]
    ams_max_units: int
    ams_slots_per_unit: int
    #: Filament slots with the most AMS units connected; 0 means external spool only.
    ams_max_slots: int
    ams_lite_max_units: int
    notes: tuple[str, ...]
    sources: tuple[Source, ...]

    @property
    def full_name(self) -> str:
        return f"{self.vendor} {self.model}"


@dataclass(frozen=True, slots=True)
class MaterialSpec:
    """A base polymer and the trade names that resolve to it."""

    key: str
    name: str
    aliases: tuple[str, ...]
    #: Generic density in g/cm^3, or ``None`` when no generic value is held.
    density_g_cm3: float | None


def normalise_printer_name(text: str) -> str:
    """Comparison form of a printer name: ``"Bambu Lab A1-mini"`` -> ``"A1 MINI"``."""
    name = _SEPARATORS.sub(" ", text.strip().upper()).strip()
    for prefix in _VENDOR_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :].strip()
    return name


def normalise_material_name(text: str) -> str:
    """Comparison form of a material name: ``" pla-cf "`` -> ``"PLA CF"``."""
    return _SEPARATORS.sub(" ", text.strip().upper()).strip()


@dataclass(frozen=True, slots=True)
class Registry:
    version: int
    printers: tuple[PrinterSpec, ...]
    materials: tuple[MaterialSpec, ...]

    def printer(self, name: str) -> PrinterSpec | None:
        """The printer called ``name`` (key, model, alias or full name), or ``None``."""
        return _printer_index(self).get(normalise_printer_name(name))

    def material(self, name: str) -> MaterialSpec | None:
        """The base polymer of ``name`` (``"PLA Silk"`` -> PLA), or ``None`` if unknown."""
        materials = {m.key: m for m in self.materials}
        key = _material_index(self).get(normalise_material_name(name))
        if key is None:
            letters = _LEADING_LETTERS.match(name.strip().upper())
            key = letters.group(0) if letters is not None else None
        return materials.get(key) if key is not None else None

    def base_polymer(self, name: str) -> str | None:
        """``"PETG HF"`` -> ``"PETG"``; ``None`` for names that resolve to no known polymer."""
        spec = self.material(name)
        return spec.key if spec is not None else None

    def density(self, name: str) -> float | None:
        """Generic density of the base polymer of ``name`` in g/cm^3, if one is held."""
        spec = self.material(name)
        return spec.density_g_cm3 if spec is not None else None


@lru_cache(maxsize=16)
def _printer_index(registry: Registry) -> dict[str, PrinterSpec]:
    index: dict[str, PrinterSpec] = {}
    for spec in registry.printers:
        for name in (spec.key, spec.model, spec.full_name, *spec.aliases):
            index[normalise_printer_name(name)] = spec
    return index


@lru_cache(maxsize=16)
def _material_index(registry: Registry) -> dict[str, str]:
    index: dict[str, str] = {}
    for spec in registry.materials:
        for name in (spec.key, *spec.aliases):
            index[normalise_material_name(name)] = spec.key
    return index


def _require(raw: Any, key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if not isinstance(raw, dict) or key not in raw:
        raise RegistryError(f"{where}: missing {key!r}")
    value = raw[key]
    if isinstance(value, bool) and kind is not bool:
        raise RegistryError(f"{where}: {key!r} has the wrong type")
    if not isinstance(value, kind):
        raise RegistryError(f"{where}: {key!r} has the wrong type")
    return value


def _strings(raw: Any, key: str, where: str) -> tuple[str, ...]:
    items = _require(raw, key, list, where)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise RegistryError(f"{where}: {key!r} must be a list of non-empty strings")
    return tuple(items)


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RegistryError(f"{where}: expected a positive number, got {value!r}")
    return float(value)


def _count(raw: Any, key: str, where: str) -> int:
    value = _require(raw, key, int, where)
    if value < 0:
        raise RegistryError(f"{where}: {key!r} must be >= 0")
    return int(value)


def _parse_printer(raw: Any, index: int) -> PrinterSpec:
    where = f"printers[{index}]"
    key = _require(raw, "key", str, where)
    where = f"printer {key}"
    volume = _require(raw, "build_volume_mm", list, where)
    if len(volume) != 3:
        raise RegistryError(f"{where}: build_volume_mm must be [x, y, z]")
    x, y, z = (_number(v, f"{where} build_volume_mm") for v in volume)
    nozzle = _require(raw, "nozzle", dict, where)
    included = _number(_require(nozzle, "included_mm", (int, float), where), f"{where} nozzle")
    options = tuple(
        sorted(_number(v, f"{where} nozzle") for v in _require(nozzle, "options_mm", list, where))
    )
    if included not in options:
        raise RegistryError(f"{where}: included nozzle {included:g} mm is not in options_mm")
    plates = _require(raw, "build_plates", dict, where)
    shipped = None if plates.get("included") is None else _strings(plates, "included", where)
    compatible = _strings(plates, "compatible", where)
    ams = _require(raw, "ams", dict, where)
    units = _count(ams, "max_units", f"{where} ams")
    per_unit = _count(ams, "slots_per_unit", f"{where} ams")
    max_slots = _count(ams, "max_slots", f"{where} ams")
    if max_slots != units * per_unit:
        raise RegistryError(f"{where}: ams max_slots must be max_units x slots_per_unit")
    sources = []
    for item in _require(raw, "sources", list, where):
        url = _require(item, "url", str, f"{where} source")
        if not url.startswith("https://"):
            raise RegistryError(f"{where}: source URL must use https, got {url!r}")
        sources.append(
            Source(
                url=url,
                retrieved=_require(item, "retrieved", str, f"{where} source"),
                covers=_require(item, "covers", str, f"{where} source"),
            )
        )
    if not sources:
        raise RegistryError(f"{where}: every printer needs at least one source")
    return PrinterSpec(
        key=key,
        vendor=_require(raw, "vendor", str, where),
        model=_require(raw, "model", str, where),
        aliases=_strings(raw, "aliases", where),
        build_volume_mm=(x, y, z),
        enclosed=_require(raw, "enclosed", bool, where),
        nozzle_material=_require(nozzle, "material", str, where),
        nozzle_included_mm=included,
        nozzle_options_mm=options,
        plates_included=shipped,
        plates_compatible=compatible,
        ams_max_units=units,
        ams_slots_per_unit=per_unit,
        ams_max_slots=max_slots,
        ams_lite_max_units=_count(ams, "ams_lite_max_units", f"{where} ams"),
        notes=_strings(raw, "notes", where),
        sources=tuple(sources),
    )


def _parse_material(raw: Any, index: int) -> MaterialSpec:
    where = f"materials[{index}]"
    key = _require(raw, "key", str, where)
    if not _LEADING_LETTERS.fullmatch(key):
        raise RegistryError(f"{where}: key must be upper-case letters, got {key!r}")
    density = raw.get("density_g_cm3") if isinstance(raw, dict) else None
    return MaterialSpec(
        key=key,
        name=_require(raw, "name", str, f"material {key}"),
        aliases=_strings(raw, "aliases", f"material {key}"),
        density_g_cm3=None if density is None else _number(density, f"material {key} density"),
    )


def _check_unique(names: dict[str, str], name: str, owner: str, what: str) -> None:
    previous = names.setdefault(name, owner)
    if previous != owner:
        raise RegistryError(f"{what} name {name!r} is used by both {previous} and {owner}")


def parse_registry(data: Any) -> Registry:
    """Validate the decoded data file and build a :class:`Registry`."""
    if not isinstance(data, dict):
        raise RegistryError("registry must be a JSON object")
    schema = _require(data, "schema_version", int, "registry")
    if schema > SCHEMA_VERSION:
        raise RegistryError(f"registry schema {schema} is newer than {SCHEMA_VERSION}")
    version = _require(data, "registry_version", int, "registry")
    printers = tuple(
        _parse_printer(raw, i) for i, raw in enumerate(_require(data, "printers", list, "registry"))
    )
    materials = tuple(
        _parse_material(raw, i)
        for i, raw in enumerate(_require(data, "materials", list, "registry"))
    )
    printer_names: dict[str, str] = {}
    for p in printers:
        for name in {p.key, p.model, p.full_name, *p.aliases}:
            _check_unique(printer_names, normalise_printer_name(name), p.key, "printer")
    material_names: dict[str, str] = {}
    for m in materials:
        for name in (m.key, *m.aliases):
            _check_unique(material_names, normalise_material_name(name), m.key, "material")
    return Registry(version=version, printers=printers, materials=materials)


@lru_cache(maxsize=4)
def _load(path: Path) -> Registry:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"{path}: {exc}") from None
    return parse_registry(data)


def load_registry(path: Path | None = None) -> Registry:
    """The registry from ``path``, by default the data file shipped next to this module."""
    return _load(path or REGISTRY_PATH)
