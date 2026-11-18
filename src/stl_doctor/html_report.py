"""Self-contained HTML report with inline-SVG orthographic views. No JavaScript, no CDN.

Each view sorts visible triangles by depth and draws them back to front (painter's algorithm),
shaded by a fixed light. For a closed, consistently wound mesh, back faces are skipped.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from . import __version__
from .analysis import Report
from .vec import Vec3, dot, normalize

_LEVELS = 8


@dataclass(frozen=True)
class View:
    title: str
    toward_viewer: Vec3  # unit vector pointing from the model to the viewer
    right: Vec3  # world direction drawn to the right
    up: Vec3  # world direction drawn upward
    light: Vec3


_S3 = 3**-0.5
_S2 = 2**-0.5
_S6 = 6**-0.5

VIEWS = (
    # Isometric from front-right-top: right = (1,1,0)/sqrt2, up = (-1,1,2)/sqrt6.
    View(
        "Isometric",
        (_S3, -_S3, _S3),
        (_S2, _S2, 0.0),
        (-_S6, _S6, 2 * _S6),
        (0.2, -0.55, 0.81),
    ),
    View("Top (looking down -Z)", (0, 0, 1), (1, 0, 0), (0, 1, 0), (0.35, -0.45, 0.82)),
    View("Front (looking along +Y)", (0, -1, 0), (1, 0, 0), (0, 0, 1), (0.35, -0.82, 0.45)),
    View("Side (looking along -X)", (1, 0, 0), (0, 1, 0), (0, 0, 1), (0.82, 0.35, 0.45)),
    # Overhangs face down, so they are only visible from below.
    View("Bottom (looking up +Z)", (0, 0, -1), (1, 0, 0), (0, -1, 0), (0.35, 0.45, -0.82)),
)

_VIEW_W = 420.0
_VIEW_H = 340.0
_PAD = 18.0

_CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1f21;--muted:#5d646b;--line:#d9dcdf;
--pass:#1f7a3d;--warn:#9a6200;--fail:#b3261e;--info:#2c5d9b;
--s0:#5b6573;--s1:#677282;--s2:#747f90;--s3:#818c9d;--s4:#8e99aa;--s5:#9ba6b6;--s6:#a9b3c2;--s7:#b7c0cd;
--o0:#8a4b05;--o1:#9b5608;--o2:#ad620b;--o3:#bf6e0f;--o4:#cf7b17;--o5:#dd8a25;--o6:#e89a38;--o7:#f0ab4f;
--t0:#4b2a8a;--t1:#563297;--t2:#623ba4;--t3:#6e45b0;--t4:#7b51bb;--t5:#895ec5;--t6:#976ccf;--t7:#a67bd8;
--p0:#8e1b16;--p1:#9f211b;--p2:#b02820;--p3:#c13026;--p4:#cf3b30;--p5:#da4a3e;--p6:#e35b4f;--p7:#ea6d62;
--hole:#e0201a;--nm:#d61ad6;--outline:#30363d}
@media (prefers-color-scheme:dark){:root{--bg:#121416;--card:#1b1e21;--fg:#e6e8ea;
--muted:#9aa3ab;--line:#30363d;--pass:#5cc47e;--warn:#e7a93a;--fail:#ff6b61;--info:#7fb0ff;
--s0:#3b434d;--s1:#454e59;--s2:#505a66;--s3:#5b6673;--s4:#677280;--s5:#737f8e;--s6:#808c9b;--s7:#8e9aa9;
--hole:#ff4d45;--nm:#ff5cff;--outline:#c9d1d9}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1340px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.verdict{display:inline-block;font-weight:700;letter-spacing:.04em;padding:2px 10px;
border-radius:4px;color:#fff;margin-right:8px}
.v-pass{background:var(--pass)}.v-warn{background:var(--warn)}.v-fail{background:var(--fail)}
.sev{font-weight:600;text-transform:uppercase;font-size:12px;letter-spacing:.04em}
.sev-error{color:var(--fail)}.sev-warning{color:var(--warn)}.sev-info{color:var(--info)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{font-size:13px;color:var(--muted);font-weight:600}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stat b{display:block;font-size:18px;font-variant-numeric:tabular-nums}
figure{margin:0}figcaption{font-size:13px;color:var(--muted);margin-bottom:6px}
svg{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--muted);margin-top:10px}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:5px;
vertical-align:-1px}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}
.loc{font-size:13px;color:var(--muted)}
"""


def _classes_css() -> str:
    rules = []
    for prefix in "sotp":
        for i in range(_LEVELS):
            rules.append(f".{prefix}{i}{{fill:var(--{prefix}{i})}}")
    rules.append("polygon{stroke-width:.4;stroke-linejoin:round}")
    for prefix in "sotp":
        for i in range(_LEVELS):
            rules.append(f".{prefix}{i}{{stroke:var(--{prefix}{i})}}")
    rules.append(".hole{stroke:var(--hole);stroke-width:2.2;stroke-linecap:round;fill:none}")
    rules.append(".nm{stroke:var(--nm);stroke-width:2.6;stroke-linecap:round;fill:none}")
    rules.append(".axis{fill:var(--muted);font-size:20px}")
    return "\n".join(rules)


def _fmt(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".") if abs(x) < 1e6 else f"{x:.0f}"


def _axis_name(v: Vec3) -> str | None:
    """``+X``/``-Z``... for an axis-aligned direction, ``None`` otherwise."""
    for i in range(3):
        if abs(v[i]) == 1:
            return ("+" if v[i] > 0 else "-") + "XYZ"[i]
    return None


def _svg_view(report: Report, view: View, scale: float, centre: tuple[float, float]) -> str:
    mesh = report.mesh
    overhang = set(report.overhang.faces)
    thin = set(report.thin.faces) if report.thin else set()
    problem = report.problem_faces
    # Back faces can be skipped only when the surface is closed and consistently outward.
    winding_ok = all(
        report.finding(code) is None
        for code in ("inconsistent_winding", "inside_out", "open_boundaries", "non_manifold_edges")
    )
    cx, cy = centre
    light = normalize(view.light)

    def project(p: Vec3) -> tuple[float, float]:
        x = (dot(p, view.right) - cx) * scale + _VIEW_W / 2
        y = _VIEW_H / 2 - (dot(p, view.up) - cy) * scale
        return x, y

    items: list[tuple[float, str]] = []
    for f in range(len(mesh.faces)):
        if mesh.is_collapsed(f):
            continue
        n = mesh.face_normal(f)
        facing = dot(n, view.toward_viewer)
        if winding_ok and facing <= 1e-9 and f not in problem:
            continue  # back face hidden behind front faces of a closed, consistent mesh
        tri = mesh.triangle(f)
        depth = sum(dot(p, view.toward_viewer) for p in tri) / 3.0
        shade = 0.25 + 0.75 * abs(dot(n, light))
        level = min(_LEVELS - 1, max(0, int(shade * _LEVELS)))
        if f in problem:
            cls = f"p{level}"
        elif f in thin:
            cls = f"t{level}"
        elif f in overhang:
            cls = f"o{level}"
        else:
            cls = f"s{level}"
        pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in map(project, tri))
        items.append((depth, f'<polygon class="{cls}" points="{pts}"/>'))
    items.sort(key=lambda item: item[0])
    parts = [
        f'<svg viewBox="0 0 {_VIEW_W:g} {_VIEW_H:g}" role="img" '
        f'aria-label="{html.escape(view.title)}" xmlns="http://www.w3.org/2000/svg">'
    ]
    parts += [svg for _, svg in items]
    for cls, edges in (("hole", report.boundary_edges), ("nm", report.non_manifold_edges)):
        for a, b in edges:
            (x1, y1), (x2, y2) = project(a), project(b)
            parts.append(
                f'<line class="{cls}" x1="{_fmt(x1)}" y1="{_fmt(y1)}" '
                f'x2="{_fmt(x2)}" y2="{_fmt(y2)}"/>'
            )
    right = _axis_name(view.right)
    up = _axis_name(view.up)
    if right and up:
        parts.append(
            f'<text class="axis" x="{_VIEW_W - 8:g}" y="{_VIEW_H - 8:g}" '
            f'text-anchor="end">{right} &#8594;</text>'
        )
        parts.append(f'<text class="axis" x="8" y="20">&#8593; {up}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _views(report: Report) -> str:
    mesh = report.mesh
    used = sorted({i for face in mesh.faces for i in face})
    pts = [mesh.vertices[i] for i in used] or [(0.0, 0.0, 0.0)]
    figs = []
    # One scale for all views so sizes are comparable between them.
    spans = []
    centres = []
    for view in VIEWS:
        xs = [dot(p, view.right) for p in pts]
        ys = [dot(p, view.up) for p in pts]
        spans.append((max(xs) - min(xs), max(ys) - min(ys)))
        centres.append(((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2))
    usable_w, usable_h = _VIEW_W - 2 * _PAD, _VIEW_H - 2 * _PAD
    scale = min(
        min(usable_w / w if w > 0 else float("inf"), usable_h / h if h > 0 else float("inf"))
        for w, h in spans
    )
    if scale == float("inf"):
        scale = 1.0
    for view, centre in zip(VIEWS, centres, strict=True):
        svg = _svg_view(report, view, scale, centre)
        figs.append(
            f'<figure class="card"><figcaption>{html.escape(view.title)}</figcaption>{svg}</figure>'
        )
    return '<div class="grid">' + "\n".join(figs) + "</div>"


def _legend() -> str:
    entries = [
        ("var(--s4)", "surface"),
        ("var(--o4)", "overhang needing support"),
        ("var(--t4)", "thin wall"),
        ("var(--p4)", "flipped / duplicate / degenerate / intersecting face"),
        ("var(--hole)", "hole boundary edge"),
        ("var(--nm)", "non-manifold edge"),
    ]
    spans = "".join(
        f'<span><span class="sw" style="background:{c}"></span>{html.escape(t)}</span>'
        for c, t in entries
    )
    return (
        f'<div class="legend">{spans}</div>'
        '<p class="muted" style="font-size:13px">Faces are drawn back to front (painter\'s '
        "algorithm). Problem edges are drawn on top, including edges hidden behind the surface."
        "</p>"
    )


def _locations(locs: list[dict[str, object]], limit: int = 5) -> str:
    if not locs:
        return ""
    shown = []
    for loc in locs[:limit]:
        if "x" in loc:
            extra = ", ".join(f"{k}={v}" for k, v in loc.items() if k not in ("x", "y", "z"))
            text = f"({loc['x']}, {loc['y']}, {loc['z']})" + (f" {extra}" if extra else "")
        else:
            text = ", ".join(f"{k}={v}" for k, v in loc.items())
        shown.append(html.escape(text))
    more = f"<br>... and {len(locs) - limit} more" if len(locs) > limit else ""
    return '<div class="loc">' + "<br>".join(shown) + more + "</div>"


def render_html(report: Report) -> str:
    s = report.stats
    verdict = report.verdict
    title = html.escape(report.path or s.get("name") or "mesh")
    size = s["size_mm"]
    opts = report.options
    stat_cells = [
        ("Size (mm)", f"{size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f}"),
        ("Volume", f"{s['volume_mm3']:.2f} mm&#179;"),
        ("Surface area", f"{s['area_mm2']:.2f} mm&#178;"),
        ("Faces / vertices", f"{s['faces']} / {s['vertices']}"),
        ("Shells", str(s["shells"])),
        ("Watertight", "yes" if s["watertight"] else "no"),
        ("Fits " + html.escape(opts.bed.name), "yes" if s["fits_bed"] else "no"),
        ("Overhang area", f"{report.overhang.area:.2f} mm&#178;"),
    ]
    stats_html = "".join(
        f'<div class="card stat"><span class="muted">{k}</span><b>{v}</b></div>'
        for k, v in stat_cells
    )
    rows = []
    for f in report.findings:
        rows.append(
            f'<tr><td><span class="sev sev-{f.severity}">{f.severity}</span></td>'
            f"<td><b>{html.escape(f.title)}</b><br><code>{f.code}</code></td>"
            f'<td class="num">{f.count}</td>'
            f"<td>{html.escape(f.message)}{_locations(f.locations)}</td></tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="4">No problems found.</td></tr>')
    orient_html = ""
    if report.orientation and report.orientation[0].overhang_area >= report.overhang.area - 1e-6:
        orient_html = (
            "<h2>Orientation suggestion (heuristic)</h2><div class='card'><p>No sampled "
            "orientation resting on a face has less overhang than the current one.</p></div>"
        )
    elif report.orientation:
        best = report.orientation[0]
        orient_html = (
            "<h2>Orientation suggestion (heuristic)</h2><div class='card'><p>"
            f"Rotating <b>{best.rot_x:.1f}&#176; about X</b>, then <b>{best.rot_y:.1f}&#176; "
            f"about Y</b> reduces overhang area from {report.overhang.area:.2f} to "
            f"{best.overhang_area:.2f} mm&#178; (bed contact {best.contact_area:.2f} mm&#178;, "
            f"height {best.height:.2f} mm).</p><p class='muted'>Chosen from "
            f"{len(report.orientation)} sampled directions by least overhang area, then largest "
            "bed contact, then lowest height. It ignores bridging, strength along layers and "
            "surface finish.</p></div>"
        )
    warnings = ""
    if s.get("warnings"):
        warnings = (
            "<h2>Reader notes</h2><div class='card'><ul>"
            + "".join(f"<li>{html.escape(w)}</li>" for w in s["warnings"])
            + "</ul></div>"
        )
    summary = report.summary()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="stl-doctor {__version__}">
<title>stl-doctor: {title}</title>
<style>{_CSS}
{_classes_css()}</style>
</head>
<body>
<main>
<h1>{title}</h1>
<p><span class="verdict v-{verdict}">{verdict.upper()}</span>{summary}.
<span class="muted">{html.escape(s["format"])} input, {s["facets_in_file"]} facets,
welded at {report.mesh.weld_tolerance:g} mm; nozzle {opts.nozzle:g} mm,
overhang limit {opts.overhang_angle:g}&#176;.</span></p>
<div class="stats">{stats_html}</div>
<h2>Views</h2>
{_views(report)}
{_legend()}
<h2>Findings</h2>
<div class="card"><table>
<thead><tr><th>Severity</th><th>Check</th><th>Count</th><th>Details</th></tr></thead>
<tbody>
{"".join(rows)}
</tbody></table></div>
{orient_html}
{warnings}
<p class="muted" style="margin-top:28px;font-size:13px">Generated by stl-doctor {__version__}.
Geometry checks are heuristics; confirm in your slicer before printing.</p>
</main>
</body>
</html>
"""
