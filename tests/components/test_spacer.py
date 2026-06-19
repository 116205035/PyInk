"""Tests for :func:`pyink.components.Spacer` (PR4)."""

from __future__ import annotations

from pyink import Box, Spacer, Text, render_to_string
from pyink.components.spacer import Spacer as SpacerDirect
from pyink.core.element import Element


def test_spacer_creates_box_element() -> None:
    el = Spacer()
    assert isinstance(el, Element)
    assert el.type == "box"
    assert el.props.get("flexGrow") == 1


def test_spacer_direct_import_matches_public() -> None:
    assert SpacerDirect is Spacer


def test_spacer_default_flex_grow_one() -> None:
    """Without ``size``, Spacer grows to fill the available main-axis space."""
    el = Spacer()
    assert el.props["flexGrow"] == 1


def test_spacer_size_sets_width() -> None:
    el = Spacer(size=5)
    assert el.props["width"] == 5
    # flexGrow is not added when an explicit size is given.
    assert "flexGrow" not in el.props


def test_spacer_expands_between_siblings() -> None:
    """Row layout: Spacer fills the gap so children end up on the edges."""
    tree = Box(
        Text("A"),
        Spacer(),
        Text("B"),
        width=10,
    )
    # 'A' at col 0, Spacer fills cols 1..8, 'B' at col 9.
    assert render_to_string(tree) == "A        B"


def test_spacer_with_explicit_size() -> None:
    """``size`` produces a fixed-width spacer of that many cells."""
    tree = Box(
        Text("A"),
        Spacer(size=3),
        Text("B"),
        alignItems="flex-start",
    )
    assert render_to_string(tree) == "A   B"


def test_spacer_in_column() -> None:
    tree = Box(
        Text("A"),
        Spacer(),
        Text("B"),
        flexDirection="column",
        height=5,
    )
    out = render_to_string(tree)
    # A on row 0, B on row 4 (Spacer fills rows 1..3 → blank lines).
    assert out == "A\n\n\n\nB"


def test_spacer_forwards_extra_props() -> None:
    el = Spacer(margin=2)
    assert el.props["margin"] == 2
    assert el.props["flexGrow"] == 1
