"""Build volumes of the printers in the shared registry (mm).

The figures come from ``print_registry.json``, the printer and material registry shared with
gcode-insight, printfarm-planner and print-forge. Each printer there carries the manufacturer
pages its published build volume was taken from.
"""

from __future__ import annotations

from dataclasses import dataclass

from .registry import load_registry


@dataclass(frozen=True)
class Bed:
    name: str
    x: float
    y: float
    z: float

    def fits(self, dims: tuple[float, float, float]) -> tuple[bool, bool]:
        """Return (fits as placed, fits after a 90 degree turn about Z)."""
        dx, dy, dz = dims
        as_is = dx <= self.x and dy <= self.y and dz <= self.z
        turned = dy <= self.x and dx <= self.y and dz <= self.z
        return as_is, turned

    def label(self) -> str:
        return f"{self.name} ({self.x:g} x {self.y:g} x {self.z:g} mm)"


PRINTERS: dict[str, Bed] = {
    p.key: Bed(p.full_name, *p.build_volume_mm) for p in load_registry().printers
}

DEFAULT_PRINTER = "p1s"


def parse_bed(text: str) -> Bed:
    """Parse ``WxDxH`` (mm), e.g. ``220x220x250``."""
    parts = text.lower().replace("*", "x").split("x")
    if len(parts) != 3:
        raise ValueError(f"bed size must look like 220x220x250, got {text!r}")
    try:
        x, y, z = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"bed size must look like 220x220x250, got {text!r}") from exc
    if min(x, y, z) <= 0:
        raise ValueError("bed dimensions must be positive")
    return Bed("custom", x, y, z)
