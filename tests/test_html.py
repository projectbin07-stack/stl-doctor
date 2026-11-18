from __future__ import annotations

from html.parser import HTMLParser

import pytest

from conftest import check
from stl_doctor.html_report import render_html

VOID = {"meta", "br", "link", "img", "input", "hr", "polygon", "line"}


class StrictTree(HTMLParser):
    """Checks that every non-void element is closed in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.counts: dict[str, int] = {}
        self.classes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.counts[tag] = self.counts.get(tag, 0) + 1
        self.classes += [v or "" for k, v in attrs if k == "class"]
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.counts[tag] = self.counts.get(tag, 0) + 1
        self.classes += [v or "" for k, v in attrs if k == "class"]

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}>, open: {self.stack[-3:]}")
            return
        self.stack.pop()


def parse(text: str) -> StrictTree:
    tree = StrictTree()
    tree.feed(text)
    tree.close()
    return tree


@pytest.mark.parametrize(
    "name",
    [
        "cube_binary.stl",
        "cube_missing_face_ascii.stl",
        "cube_flipped_face_binary.stl",
        "overhang_t_binary.stl",
        "thin_plate_ascii.stl",
        "nonmanifold_edge_binary.stl",
        "overlapping_cubes_binary.stl",
    ],
)
def test_report_is_well_formed(name: str) -> None:
    text = render_html(check(name))
    tree = parse(text)
    assert tree.errors == []
    assert tree.stack == []
    assert tree.counts["svg"] == 5
    assert "<script" not in text
    assert "http://" not in text.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "https://" not in text
    assert "prefers-color-scheme:dark" in text


def test_report_highlights_problems() -> None:
    holes = parse(render_html(check("cube_missing_face_binary.stl")))
    # 4 boundary edges drawn in each of 5 views.
    assert holes.classes.count("hole") == 20
    t = parse(render_html(check("overhang_t_binary.stl")))
    assert any(c.startswith("o") and c[1:].isdigit() for c in t.classes)
    flipped = parse(render_html(check("cube_flipped_face_binary.stl")))
    assert any(c.startswith("p") and c[1:].isdigit() for c in flipped.classes)
    thin = parse(render_html(check("thin_plate_binary.stl")))
    assert any(c.startswith("t") and c[1:].isdigit() for c in thin.classes)
    nm = parse(render_html(check("nonmanifold_edge_binary.stl")))
    assert nm.classes.count("nm") == 5


def test_back_faces_are_culled_for_clean_mesh() -> None:
    tree = parse(render_html(check("cube_binary.stl")))
    # Each axis view of a cube sees one square (2 triangles x 4 views); the isometric view sees
    # three squares (6 triangles).
    assert tree.counts["polygon"] == 14


def test_report_escapes_file_name() -> None:
    report = check("cube_binary.stl")
    report.path = "<b>evil</b>.stl"
    text = render_html(report)
    assert "<b>evil</b>" not in text
    assert "&lt;b&gt;evil&lt;/b&gt;.stl" in text


def test_report_mentions_orientation_and_verdict() -> None:
    text = render_html(check("overhang_t_binary.stl"))
    assert "Orientation suggestion (heuristic)" in text
    assert "v-warn" in text
    no_gain = render_html(check("overlapping_cubes_binary.stl"))
    assert "No sampled orientation" in no_gain or "reduces overhang area" in no_gain
