# Contributing

Bug reports, meshes that are analysed wrongly, and pull requests are welcome.

## Development setup

```console
git clone https://github.com/projectbin07-stack/stl-doctor.git
cd stl-doctor
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
```

## Checks

All four must pass; CI runs them on Ubuntu and Windows with Python 3.10 and 3.13.

```console
ruff check .
ruff format --check .
mypy
pytest --cov
```

## Guidelines

- Keep the runtime free of third-party dependencies. numpy may only be used as an optional
  speed-up with a pure-Python path that is tested to give the same result.
- New behaviour needs a test whose expected value is derived by hand from the geometry, with the
  derivation in a comment. Do not assert against numbers copied from the tool's own output.
- Fixtures are generated: change `tests/make_fixtures.py`, run `python tests/make_fixtures.py`, and
  commit the regenerated files. Do not add third-party models. If a real model triggers a bug,
  reduce it to the few triangles that matter and build them in the generator or the test.
- Do not state accuracy or performance figures in documentation unless you measured them and say
  how.
- Keep commits small with imperative messages (`Add ...`, `Fix ...`).

## Reporting a wrong result

Include the command you ran, the `--json` output, what you expected, and if possible the smallest
mesh that reproduces it (a handful of triangles written as ASCII STL is ideal).
