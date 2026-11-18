"""Time ``stl-doctor`` on generated tori of increasing size.

Usage: ``python scripts/benchmark.py [faces ...]`` (default 2000 20000 80000). The torus is a
closed, valid mesh, so this measures the cost of a clean run, not of a pathological one.
"""

from __future__ import annotations

import math
import sys
import time

from stl_doctor.analysis import Options, analyze
from stl_doctor.mesh import Mesh
from stl_doctor.printability import numpy_available
from stl_doctor.vec import Triangle, Vec3


def torus(faces: int, big: float = 30.0, small: float = 10.0) -> list[Triangle]:
    # faces = 2 * nu * nv with nu = 2 * nv
    nv = max(3, round(math.sqrt(faces / 4)))
    nu = 2 * nv

    def p(i: int, j: int) -> Vec3:
        u = 2 * math.pi * (i % nu) / nu
        v = 2 * math.pi * (j % nv) / nv
        r = big + small * math.cos(v)
        return (r * math.cos(u), r * math.sin(u), small + small * math.sin(v))

    tris: list[Triangle] = []
    for i in range(nu):
        for j in range(nv):
            a, b, c, d = p(i, j), p(i + 1, j), p(i + 1, j + 1), p(i, j + 1)
            tris += [(a, b, c), (a, c, d)]
    return tris


def main(sizes: list[int]) -> None:
    print(f"python {sys.version.split()[0]}, numpy available: {numpy_available()}")
    for n in sizes:
        tris = torus(n)
        t0 = time.perf_counter()
        mesh = Mesh.from_triangles(tris)
        t1 = time.perf_counter()
        fast = analyze(mesh, Options(check_intersections=False))
        t2 = time.perf_counter()
        full = analyze(mesh, Options())
        t3 = time.perf_counter()
        print(
            f"{len(tris):>7} faces: weld {t1 - t0:6.2f} s | check without intersections "
            f"{t2 - t1:6.2f} s | full check {t3 - t2:6.2f} s | verdict {full.verdict}"
            f" ({fast.verdict})"
        )


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [2000, 20000, 80000])
