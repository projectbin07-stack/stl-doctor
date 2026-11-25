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

Schema version 2 adds connection facts per printer (:class:`ConnectionFacts`) and the AMS
models (:class:`AmsModel`). Each fact carries its sources and whether they are the
manufacturer's (``"official"``) or third-party reverse engineering (``"community"``); a fact
that no source settles is absent (``None``), never guessed. Data files of schema 1 still load,
with no connection facts and no AMS models.

The names in ``__all__`` are a public API: they are only removed or changed incompatibly in a
new major version of gcode-insight.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

__all__ = [
    "REGISTRY_PATH",
    "SCHEMA_VERSION",
    "AmsModel",
    "ConnectionFacts",
    "Fact",
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
#: Newest ``schema_version`` of the data file this code understands (it reads 1 and 2).
SCHEMA_VERSION = 2

_V = TypeVar("_V", str, int, bool)

_SEPARATORS = re.compile(r"[\s_\-]+")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CONNECTION_FACTS = (
    "slicer_model_id",
    "reported_project_name",
    "camera_stream",
    "camera_port",
    "chamber_temperature",
)
_CAMERA_STREAMS = frozenset({"rtsps", "jpeg-tls"})
_AMS_KEYS = frozenset({"ams", "ams-lite", "ams-2-pro", "ams-ht"})
_LEADING_LETTERS = re.compile(r"[A-Z]+")
_VENDOR_PREFIXES = ("BAMBU LAB ", "BAMBULAB ", "BAMBU ")


class RegistryError(ValueError):
    """The registry data file is missing, malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class Source:
    """Where a figure comes from: an https URL, the date it was read, and what it states."""

    url: str
    #: ISO date (``YYYY-MM-DD``) the page was read.
    retrieved: str
    covers: str


@dataclass(frozen=True, slots=True)
class Fact(Generic[_V]):
    """One sourced value, such as a printer's camera port."""

    value: _V
    #: ``"official"``: Bambu Lab's own pages or source code. ``"community"``: third-party
    #: reverse engineering only; it describes observed behaviour and may change with firmware.
    basis: Literal["official", "community"]
    sources: tuple[Source, ...]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionFacts:
    """What a fleet manager needs to know to talk to a printer. ``None`` means unknown.

    Absent facts were left out on purpose because no source settles them; see the
    ``connection_note`` in the data file and ``docs/registry.md`` in gcode-insight.
    """

    #: ``model_id`` in Bambu Studio's machine profile for this printer (``"C12"`` for the P1S).
    slicer_model_id: Fact[str] | None = None
    #: ``project_name`` of the printer's ``esp32`` module in its ``get_version`` reply.
    reported_project_name: Fact[str] | None = None
    #: How the LAN camera streams: ``"rtsps"`` or ``"jpeg-tls"`` (JPEG frames over a TLS
    #: socket).
    camera_stream: Fact[str] | None = None
    camera_port: Fact[int] | None = None
    #: Whether the status report carries a usable chamber temperature.
    chamber_temperature: Fact[bool] | None = None


@dataclass(frozen=True, slots=True)
class AmsModel:
    """A Bambu Lab AMS model as a fleet manager sees it."""

    #: ``ams``, ``ams-lite``, ``ams-2-pro`` or ``ams-ht``.
    key: str
    name: str
    #: Module name prefix in the printer's ``get_version`` reply (``"ams_f1"`` for
    #: ``ams_f1/0``), which tells the AMS models apart.
    get_version_module: Fact[str] | None = None


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
    #: Schema 2 and later; empty (every fact ``None``) for schema 1 data.
    connection: ConnectionFacts = field(default_factory=ConnectionFacts)

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
    """The parsed data file. Build it with :func:`load_registry` or :func:`parse_registry`."""

    #: ``registry_version``: bumped whenever the data changes.
    version: int
    printers: tuple[PrinterSpec, ...]
    materials: tuple[MaterialSpec, ...]
    ams_models: tuple[AmsModel, ...] = ()
    #: ``schema_version`` of the data file (the layout, not the data).
    schema_version: int = 1

    def ams_model(self, key: str) -> AmsModel | None:
        """The AMS model with this key (``"ams-lite"``), ignoring case, or ``None``."""
        wanted = key.strip().lower()
        return next((m for m in self.ams_models if m.key == wanted), None)

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


def _parse_sources(raw: Any, where: str) -> tuple[Source, ...]:
    sources = []
    for item in _require(raw, "sources", list, where):
        url = _require(item, "url", str, f"{where} source")
        if not url.startswith("https://"):
            raise RegistryError(f"{where}: source URL must use https, got {url!r}")
        retrieved = _require(item, "retrieved", str, f"{where} source")
        if not _ISO_DATE.fullmatch(retrieved):
            raise RegistryError(f"{where}: retrieved must be YYYY-MM-DD, got {retrieved!r}")
        sources.append(
            Source(url=url, retrieved=retrieved, covers=_require(item, "covers", str, where))
        )
    return tuple(sources)


def _fact(raw: Any, key: str, kind: type[_V], where: str) -> Fact[_V] | None:
    if not isinstance(raw, dict) or key not in raw:
        return None
    where = f"{where} {key}"
    item = raw[key]
    value = _require(item, "value", kind, where)
    basis = _require(item, "basis", str, where)
    if basis not in ("official", "community"):
        raise RegistryError(f"{where}: basis must be 'official' or 'community', got {basis!r}")
    sources = _parse_sources(item, where)
    if not sources:
        raise RegistryError(f"{where}: every fact needs at least one source")
    note = item.get("note")
    if note is not None and not isinstance(note, str):
        raise RegistryError(f"{where}: 'note' must be a string")
    return Fact(
        value=value,
        basis="official" if basis == "official" else "community",
        sources=sources,
        note=note,
    )


def _parse_connection(raw: Any, where: str) -> ConnectionFacts:
    if not isinstance(raw, dict) or "connection" not in raw:
        return ConnectionFacts()
    facts = _require(raw, "connection", dict, where)
    where = f"{where} connection"
    unknown = set(facts) - set(_CONNECTION_FACTS)
    if unknown:
        raise RegistryError(f"{where}: unknown facts {sorted(unknown)}")
    connection = ConnectionFacts(
        slicer_model_id=_fact(facts, "slicer_model_id", str, where),
        reported_project_name=_fact(facts, "reported_project_name", str, where),
        camera_stream=_fact(facts, "camera_stream", str, where),
        camera_port=_fact(facts, "camera_port", int, where),
        chamber_temperature=_fact(facts, "chamber_temperature", bool, where),
    )
    stream = connection.camera_stream
    if stream is not None and stream.value not in _CAMERA_STREAMS:
        raise RegistryError(f"{where}: camera_stream must be one of {sorted(_CAMERA_STREAMS)}")
    port = connection.camera_port
    if port is not None and not 0 < port.value < 65536:
        raise RegistryError(f"{where}: camera_port must be 1-65535")
    return connection


def _parse_ams_model(raw: Any, index: int) -> AmsModel:
    where = f"ams_models[{index}]"
    key = _require(raw, "key", str, where)
    if key not in _AMS_KEYS:
        raise RegistryError(f"{where}: key must be one of {sorted(_AMS_KEYS)}, got {key!r}")
    where = f"AMS model {key}"
    return AmsModel(
        key=key,
        name=_require(raw, "name", str, where),
        get_version_module=_fact(raw, "get_version_module", str, where),
    )


def _parse_printer(raw: Any, index: int, schema: int) -> PrinterSpec:
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
    sources = _parse_sources(raw, where)
    if not sources:
        raise RegistryError(f"{where}: every printer needs at least one source")
    if schema < 2 and isinstance(raw, dict) and "connection" in raw:
        raise RegistryError(f"{where}: 'connection' needs schema_version 2")
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
        sources=sources,
        connection=_parse_connection(raw, where),
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
    if schema < 1:
        raise RegistryError(f"registry schema {schema} is not valid")
    version = _require(data, "registry_version", int, "registry")
    printers = tuple(
        _parse_printer(raw, i, schema)
        for i, raw in enumerate(_require(data, "printers", list, "registry"))
    )
    ams_models: tuple[AmsModel, ...] = ()
    if "ams_models" in data:
        if schema < 2:
            raise RegistryError("registry: 'ams_models' needs schema_version 2")
        ams_models = tuple(
            _parse_ams_model(raw, i)
            for i, raw in enumerate(_require(data, "ams_models", list, "registry"))
        )
        keys = [m.key for m in ams_models]
        if len(set(keys)) != len(keys):
            raise RegistryError("registry: duplicate AMS model key")
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
    return Registry(
        version=version,
        printers=printers,
        materials=materials,
        ams_models=ams_models,
        schema_version=schema,
    )


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
