"""The committed fixtures must be exactly what ``make_fixtures.py`` generates."""

from __future__ import annotations

import zipfile
from pathlib import Path

import make_fixtures
from conftest import FIXTURES


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_committed_fixtures_match_generator(tmp_path: Path) -> None:
    written = make_fixtures.generate(tmp_path)
    assert len(written) == 21
    for path in written:
        committed = FIXTURES / path.name
        assert committed.exists(), f"{path.name} missing; run python tests/make_fixtures.py"
        if path.suffix == ".3mf":
            # Zip container bytes depend on the platform and zlib; compare the parts instead.
            assert _members(committed) == _members(path), f"{path.name} is stale"
        else:
            assert committed.read_bytes() == path.read_bytes(), f"{path.name} is stale"


def test_binary_fixture_headers_start_with_solid() -> None:
    assert (FIXTURES / "cube_binary.stl").read_bytes().startswith(b"solid cube")
