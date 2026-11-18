"""Small 3-vector helpers on plain tuples (no numpy needed)."""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
Triangle = tuple[Vec3, Vec3, Vec3]

ZERO: Vec3 = (0.0, 0.0, 0.0)


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def distance(a: Vec3, b: Vec3) -> float:
    return length(sub(a, b))


def normalize(a: Vec3) -> Vec3:
    """Return ``a`` scaled to unit length, or the zero vector if ``a`` has no length."""
    n = length(a)
    if n == 0.0:
        return ZERO
    return (a[0] / n, a[1] / n, a[2] / n)


def triangle_cross(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    """Un-normalised normal of triangle ``abc`` (right-hand rule); length is twice the area."""
    return cross(sub(b, a), sub(c, a))


def triangle_area(a: Vec3, b: Vec3, c: Vec3) -> float:
    return 0.5 * length(triangle_cross(a, b, c))


def triangle_normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    return normalize(triangle_cross(a, b, c))


def centroid(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    return ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)


def is_finite(a: Vec3) -> bool:
    return math.isfinite(a[0]) and math.isfinite(a[1]) and math.isfinite(a[2])


def rotation_to_z(up: Vec3) -> tuple[float, float]:
    """Angles (degrees) of a rotation about X followed by a rotation about Y that maps ``up`` to +Z.

    ``up`` must be a unit vector. The rotation is ``Ry(b) @ Rx(a)``.
    """
    ux, uy, uz = up
    a = math.atan2(uy, uz)
    r = math.hypot(uy, uz)
    b = math.atan2(-ux, r)
    # Rounding and "+ 0.0" keep float noise from printing as "-0.00".
    return round(math.degrees(a), 9) + 0.0, round(math.degrees(b), 9) + 0.0


def rotate_xy(p: Vec3, ax_deg: float, by_deg: float) -> Vec3:
    """Rotate ``p`` about X by ``ax_deg`` and then about Y by ``by_deg`` (right-handed)."""
    a = math.radians(ax_deg)
    b = math.radians(by_deg)
    ca, sa = math.cos(a), math.sin(a)
    x, y, z = p
    y, z = y * ca - z * sa, y * sa + z * ca
    cb, sb = math.cos(b), math.sin(b)
    x, z = x * cb + z * sb, -x * sb + z * cb
    return (x, y, z)
