"""Build volumes of common printers (mm). Values are the manufacturers' published build volumes."""

from __future__ import annotations

from dataclasses import dataclass


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
    "p1s": Bed("Bambu Lab P1S", 256, 256, 256),
    "p1p": Bed("Bambu Lab P1P", 256, 256, 256),
    "x1c": Bed("Bambu Lab X1 Carbon", 256, 256, 256),
    "a1": Bed("Bambu Lab A1", 256, 256, 256),
    "a1-mini": Bed("Bambu Lab A1 mini", 180, 180, 180),
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
