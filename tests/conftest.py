from __future__ import annotations

from pathlib import Path

import pytest

from stl_doctor.analysis import Options, Report, analyze
from stl_doctor.mesh import Mesh, load_mesh

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FORMATS = ["ascii", "binary"]


def fixture_path(name: str) -> Path:
    return FIXTURES / name


def check(name: str, **opts: object) -> Report:
    mesh = load_mesh(FIXTURES / name)
    return analyze(mesh, Options(**opts), path=name)  # type: ignore[arg-type]


def load(name: str) -> Mesh:
    return load_mesh(FIXTURES / name)


@pytest.fixture(params=FORMATS)
def fmt(request: pytest.FixtureRequest) -> str:
    return str(request.param)
