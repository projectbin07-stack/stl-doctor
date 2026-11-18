# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-19

### Added

- ASCII and binary STL reader with content-based format detection (binary files whose header
  starts with `solid` are handled), clear errors for truncated and malformed files, and NaN/inf
  facet reporting. STL writer (ASCII and binary) with recomputed normals.
- 3MF reader for the first build item, following components across model parts and applying
  transforms.
- Vertex welding with a tolerance (spatial hash), indexed mesh and edge map.
- Checks: non-manifold edges, holes with loop extraction and perimeter, inconsistent winding
  (BFS per shell), inside-out shells, stored-vs-computed normals, degenerate and duplicate faces,
  multiple and tiny shells, self-intersections (uniform-grid broad phase), bounding box and bed
  fit for Bambu Lab P1S/P1P/X1C/A1/A1 mini or a custom bed, volume and surface area.
- Printability: overhang area and support footprint estimate, thin-wall detection by inward ray
  casting, heuristic best-orientation search (numpy optional).
- Repairs written to a new file: welding, degenerate/duplicate removal, winding unification,
  inside-out inversion, planar hole filling by ear clipping, normal recomputation, with a change log.
- `stl-doctor check | repair | orient` CLI with text and JSON output and CI exit codes.
- Self-contained HTML report with five inline-SVG views, light/dark themes and no JavaScript.
- Fixture generator, tests with hand-derived expected values, benchmark script.

[0.1.0]: https://github.com/projectbin07-stack/stl-doctor/releases/tag/v0.1.0
