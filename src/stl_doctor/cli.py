"""Command-line interface: ``stl-doctor check | repair | orient``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from . import __version__
from .analysis import ERROR, INFO, WARNING, Options, Report, analyze
from .html_report import render_html
from .mesh import DEFAULT_WELD_TOLERANCE, Mesh, load_mesh
from .printability import best_orientations
from .printers import DEFAULT_PRINTER, PRINTERS, Bed, parse_bed
from .repair import repair
from .stlio import STLError, write_stl
from .vec import rotate_xy

EXIT_OK = 0
EXIT_WARN = 1
EXIT_ERROR = 2

_MARK = {ERROR: "FAIL", WARNING: "WARN", INFO: "INFO"}


def _positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be positive: {text!r}")
    return value


def _non_negative(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"must not be negative: {text!r}")
    return value


def _angle(text: str) -> float:
    value = _non_negative(text)
    if value >= 90:
        raise argparse.ArgumentTypeError("overhang angle must be below 90 degrees")
    return value


def _bed_arg(text: str) -> Bed:
    try:
        return parse_bed(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stl-doctor",
        description="Inspect STL/3MF meshes for 3D-printing problems before slicing.",
    )
    parser.add_argument("--version", action="version", version=f"stl-doctor {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    weld = argparse.ArgumentParser(add_help=False)
    weld.add_argument(
        "--weld-tol",
        type=_non_negative,
        default=DEFAULT_WELD_TOLERANCE,
        metavar="MM",
        help=f"merge vertices closer than this (default {DEFAULT_WELD_TOLERANCE:g} mm; "
        "0 = exact matches only)",
    )

    check = sub.add_parser(
        "check",
        parents=[weld],
        help="analyse a mesh and print a verdict",
        description="Analyse a mesh. Exit code: 0 pass, 1 warnings (with --strict), 2 errors.",
    )
    check.add_argument("file", help="STL (ASCII or binary) or 3MF file")
    check.add_argument(
        "--printer",
        choices=sorted(PRINTERS),
        default=DEFAULT_PRINTER,
        help=f"build volume to check against (default {DEFAULT_PRINTER})",
    )
    check.add_argument("--bed", type=_bed_arg, metavar="WxDxH", help="custom build volume in mm")
    check.add_argument("--nozzle", type=_positive, default=0.4, metavar="MM", help="default 0.4")
    check.add_argument(
        "--thin-factor",
        type=_positive,
        default=2.0,
        metavar="N",
        help="report walls thinner than N x nozzle (default 2)",
    )
    check.add_argument(
        "--overhang",
        type=_angle,
        default=45.0,
        metavar="DEG",
        help="overhang limit measured from vertical (default 45)",
    )
    check.add_argument(
        "--tiny-shell",
        type=_positive,
        default=1.0,
        metavar="MM",
        help="flag extra shells smaller than this in every direction (default 1)",
    )
    check.add_argument("--report", metavar="HTML", help="write a self-contained HTML report")
    check.add_argument("--json", action="store_true", help="print JSON instead of text")
    check.add_argument("--strict", action="store_true", help="exit 1 when there are warnings")
    check.add_argument(
        "--no-intersections", action="store_true", help="skip the self-intersection test"
    )
    check.add_argument("--no-thin-walls", action="store_true", help="skip the thin-wall test")

    rep = sub.add_parser(
        "repair",
        parents=[weld],
        help="write a repaired copy of a mesh",
        description="Weld vertices, drop degenerate/duplicate faces, unify winding, fill planar "
        "holes and recompute normals. The input file is never modified.",
    )
    rep.add_argument("input", help="STL (ASCII or binary) or 3MF file")
    rep.add_argument("output", help="where to write the repaired STL")
    rep.add_argument("--ascii", action="store_true", help="write ASCII STL (default binary)")
    rep.add_argument("--no-fill", action="store_true", help="do not fill holes")
    rep.add_argument("--json", action="store_true", help="print the change log as JSON")
    rep.add_argument("--force", action="store_true", help="overwrite OUTPUT if it exists")

    ori = sub.add_parser(
        "orient",
        parents=[weld],
        help="suggest a print orientation with less overhang (heuristic)",
        description="Score sampled 'up' directions by overhang area (then bed contact, then "
        "height) and list the best. A heuristic: it ignores bridging, strength and finish.",
    )
    ori.add_argument("file", help="STL (ASCII or binary) or 3MF file")
    ori.add_argument(
        "--overhang",
        type=_angle,
        default=45.0,
        metavar="DEG",
        help="overhang limit measured from vertical (default 45)",
    )
    ori.add_argument(
        "--samples",
        type=int,
        default=200,
        metavar="N",
        help="directions to try (default 200, plus the 6 axis directions)",
    )
    ori.add_argument(
        "--top", type=int, default=3, metavar="N", help="candidates to list (default 3)"
    )
    ori.add_argument(
        "-o", "--output", metavar="STL", help="write the mesh rotated to the best orientation"
    )
    ori.add_argument("--json", action="store_true", help="print JSON instead of text")
    return parser


def format_text(report: Report) -> str:
    s = report.stats
    opts = report.options
    size = s["size_mm"]
    lines = [
        f"stl-doctor {__version__}  {report.path}",
        f"  format     {s['format']}, {s['facets_in_file']} facets in file",
        f"  mesh       {s['faces']} faces, {s['vertices']} vertices "
        f"(welded at {report.mesh.weld_tolerance:g} mm), {s['shells']} shell(s)",
        f"  size       {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} mm, "
        + ("fits " if s["fits_bed"] else "does NOT fit ")
        + opts.bed.label(),
        f"  volume     {s['volume_mm3']:.2f} mm^3"
        + ("" if s["watertight"] else "  (surface not closed and consistent: unreliable)")
        + (
            "  (sum of overlapping shells: the printed volume is smaller)"
            if s["volume_is_sum_of_overlapping_shells"]
            else ""
        ),
        f"  area       {s['area_mm2']:.2f} mm^2",
    ]
    for w in s["warnings"]:
        lines.append(f"  note       {w}")
    lines.append("")
    if not report.findings:
        lines.append("  no problems found")
    for f in report.findings:
        lines.append(f"  {_MARK[f.severity]}  {f.title} [{f.code}]: {f.count}")
        lines.append(f"        {f.message}")
        for loc in f.locations[:3]:
            if "x" in loc:
                extra = ", ".join(f"{k}={v}" for k, v in loc.items() if k not in ("x", "y", "z"))
                lines.append(f"        at ({loc['x']}, {loc['y']}, {loc['z']}) {extra}".rstrip())
            else:
                lines.append("        " + ", ".join(f"{k}={v}" for k, v in loc.items()))
        if len(f.locations) > 3:
            lines.append(f"        ... {len(f.locations) - 3} more (see --json or --report)")
    if report.orientation:
        best = report.orientation[0]
        lines.append("")
        if best.overhang_area < report.overhang.area - 1e-6:
            lines.append(
                f"  orientation (heuristic): rotate {best.rot_x:.1f} deg about X, then "
                f"{best.rot_y:.1f} deg about Y -> overhang {report.overhang.area:.2f} -> "
                f"{best.overhang_area:.2f} mm^2"
            )
        else:
            lines.append(
                "  orientation (heuristic): no sampled orientation resting on a face has less "
                "overhang than the current one"
            )
    lines.append("")
    lines.append(f"verdict: {report.verdict.upper()} ({report.summary()})")
    return "\n".join(lines)


def _err(stream: TextIO, msg: str) -> None:
    print(f"stl-doctor: error: {msg}", file=stream)


def cmd_check(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    mesh = load_mesh(args.file, args.weld_tol)
    opts = Options(
        bed=args.bed or PRINTERS[args.printer],
        nozzle=args.nozzle,
        thin_factor=args.thin_factor,
        overhang_angle=args.overhang,
        tiny_shell=args.tiny_shell,
        check_intersections=not args.no_intersections,
        check_thin_walls=not args.no_thin_walls,
    )
    report = analyze(mesh, opts, path=str(args.file))
    if args.report:
        Path(args.report).write_text(render_html(report), encoding="utf-8")
    if args.json:
        out.write(json.dumps(report.to_dict(), indent=2) + "\n")
    else:
        out.write(format_text(report) + "\n")
        if args.report:
            out.write(f"report written to {args.report}\n")
    return report.exit_code(args.strict)


def cmd_repair(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    src = Path(args.input)
    dst = Path(args.output)
    if dst.exists() and src.resolve() == dst.resolve():
        _err(err, "refusing to overwrite the input file; choose a different OUTPUT")
        return EXIT_ERROR
    if dst.exists() and not args.force:
        _err(err, f"{dst} exists; pass --force to overwrite it")
        return EXIT_ERROR
    mesh = load_mesh(src, args.weld_tol)
    before = analyze(mesh, Options(check_thin_walls=False, suggest_orientation=False))
    fixed, log = repair(mesh, fill_holes=not args.no_fill)
    write_stl(dst, fixed.triangles(), ascii=args.ascii, name=mesh.name or "stl-doctor")
    after = analyze(fixed, Options(check_thin_walls=False, suggest_orientation=False))
    if args.json:
        payload = {
            "input": str(src),
            "output": str(dst),
            "changes": log.to_dict(),
            "before": {"verdict": before.verdict, "stats": before.stats},
            "after": {"verdict": after.verdict, "stats": after.stats},
            "remaining": [f.to_dict(10) for f in after.findings],
        }
        out.write(json.dumps(payload, indent=2) + "\n")
    else:
        out.write(f"repaired {src} -> {dst}\n")
        for line in log.lines():
            out.write(f"  {line}\n")
        out.write(
            f"  watertight: {before.stats['watertight']} -> {after.stats['watertight']}, "
            f"volume {before.stats['volume_mm3']:.2f} -> {after.stats['volume_mm3']:.2f} mm^3\n"
        )
        remaining = [f for f in after.findings if f.severity == ERROR]
        for f in remaining:
            out.write(f"  still FAIL: {f.title} ({f.count})\n")
    return EXIT_ERROR if after.verdict == "fail" else EXIT_OK


def _rotated(mesh: Mesh, rx: float, ry: float) -> list[tuple[tuple[float, float, float], ...]]:
    tris = [tuple(rotate_xy(p, rx, ry) for p in tri) for tri in mesh.triangles()]
    min_z = min(p[2] for tri in tris for p in tri)
    return [tuple((p[0], p[1], p[2] - min_z) for p in tri) for tri in tris]


def cmd_orient(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    if args.samples < 0 or args.top < 1:
        _err(err, "--samples must be >= 0 and --top >= 1")
        return EXIT_ERROR
    mesh = load_mesh(args.file, args.weld_tol)
    faces = [f for f in range(len(mesh.faces)) if mesh.face_area(f) > 0]
    cands = best_orientations(mesh, faces, args.overhang, args.samples)
    current = next(c for c in cands if c.up == (0.0, 0.0, 1.0))
    best = cands[0]
    if args.output:
        tris = _rotated(mesh, best.rot_x, best.rot_y)
        write_stl(args.output, [(t[0], t[1], t[2]) for t in tris], name=mesh.name or "oriented")
    if args.json:
        payload = {
            "file": str(args.file),
            "heuristic": True,
            "samples": len(cands),
            "current": {
                "overhang_mm2": round(current.overhang_area, 4),
                "height_mm": round(current.height, 4),
            },
            "candidates": [
                {
                    "up_vector": [round(c, 5) for c in cand.up],
                    "rotate_x_deg": round(cand.rot_x, 2),
                    "then_rotate_y_deg": round(cand.rot_y, 2),
                    "overhang_mm2": round(cand.overhang_area, 4),
                    "bed_contact_mm2": round(cand.contact_area, 4),
                    "height_mm": round(cand.height, 4),
                }
                for cand in cands[: args.top]
            ],
            "output": args.output,
        }
        out.write(json.dumps(payload, indent=2) + "\n")
        return EXIT_OK
    out.write(f"{args.file}: {len(cands)} orientations sampled (heuristic)\n")
    out.write(
        f"  current: overhang {current.overhang_area:.2f} mm^2, height {current.height:.2f} mm\n"
    )
    for i, c in enumerate(cands[: args.top], start=1):
        out.write(
            f"  #{i}: rotate {c.rot_x:7.2f} deg about X, then {c.rot_y:7.2f} deg about Y -> "
            f"overhang {c.overhang_area:.2f} mm^2, bed contact {c.contact_area:.2f} mm^2, "
            f"height {c.height:.2f} mm\n"
        )
    if args.output:
        out.write(f"  wrote best orientation to {args.output}\n")
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None, out: TextIO | None = None, err: TextIO | None = None
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    handlers = {"check": cmd_check, "repair": cmd_repair, "orient": cmd_orient}
    try:
        return handlers[args.command](args, out, err)
    except STLError as exc:
        _err(err, f"{getattr(args, 'file', None) or getattr(args, 'input', '')}: {exc}")
        return EXIT_ERROR
    except OSError as exc:
        _err(err, str(exc))
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
