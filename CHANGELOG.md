# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-11-25

### Changed

- The vendored printer and material registry (`registry.py`, `print_registry.json`) is now the
  copy from gcode-insight v0.4.0, and the `registry` CI job compares against that tag. It is
  registry schema 2: each printer also carries connection facts (slicer model code, camera
  stream and port, chamber temperature where a source settles it, each marked official or
  community) and the registry lists the AMS models. stl-doctor does not use the new fields. The
  build volumes are unchanged, and `stl-doctor check --printer a1-mini --json` gives
  byte-identical output to 0.3.0 on all 23 test fixtures.

## [0.3.0] - 2026-11-25

### Added

- `units` warning for models whose largest dimension is below 2 mm or above 1000 mm (audit #47).
  STL has no unit field, so a part exported in inches or metres arrives 25.4 or 1000 times too
  small. The warning lists the rescalings (x25.4, x10, x1000, or /25.4, /1000 for oversize
  models) and whether each would fit the selected bed. Before and after, on the audit repro
  `cube_inch.stl` (a 20 mm cube written in inches):

  ```text
  0.2.0  verdict: WARN (0 errors, 1 warning, 1 note)   thin_walls only
  0.3.0  verdict: WARN (0 errors, 2 warnings, 1 note)  units: "x25.4 (inches): 20 x 20 x 20 mm,
         fits the bed" + thin_walls
  ```

- `--printer` accepts registry names and aliases (`X1C`, `"A1 mini"`) as well as keys.

### Changed

- Build volumes now come from the printer and material registry shared with gcode-insight,
  printfarm-planner and print-forge (audit #49). `registry.py` and `print_registry.json` are
  vendored unchanged from gcode-insight v0.3.0, and a new CI job fails if they drift from that
  tag. The five volumes are the same as before; each printer now carries its manufacturer
  sources.

## [0.2.0] - 2026-11-24

### Fixed

- A correctly modelled internal void (an inner shell with inward normals) was reported as
  `inside_out`, and `stl-doctor repair` inverted it, which filled the cavity in: a 20 mm cube with
  a 10 mm void went from 7000 to 9000 mm^3. Whether a shell is inside out is now decided from its
  winding *and* the shells that contain it, in both `check` and `repair`. As a side effect the
  overhang check now sees the cavity ceiling, not its floor, as the downward surface.
- 3MF files were read only up to the first build item, silently. A print-forge plate of 84 beads
  was checked as one bead and passed. Every build item is now read with its transform.

### Added

- Per-object breakdown for multi-object 3MF files (`objects` in the JSON, one line per failing
  item in the text output).
- `internal_voids` and `embedded_shells` info findings; `role` and `inside` per shell, and
  `voids` in the stats.
- `volume_is_sum_of_overlapping_shells` in the stats, and a note on the volume line, when shells
  intersect or are buried in each other (the summed volume then overstates the printed volume).

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

[0.4.0]: https://github.com/projectbin07-stack/stl-doctor/releases/tag/v0.4.0
[0.3.0]: https://github.com/projectbin07-stack/stl-doctor/releases/tag/v0.3.0
[0.2.0]: https://github.com/projectbin07-stack/stl-doctor/releases/tag/v0.2.0
[0.1.0]: https://github.com/projectbin07-stack/stl-doctor/releases/tag/v0.1.0
