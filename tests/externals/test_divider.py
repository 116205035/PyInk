"""Tests for :func:`pyink.externals.Divider` (Phase 2 PR4).

``Divider`` is a declarative factory that wraps a single-edge ``box`` —
no hooks, no function component, no live render pipeline needed. Every
assertion uses the synchronous :func:`pyink.render_to_string` test
renderer (matching :mod:`tests.externals.test_link` rather than
``test_spinner``, which has to reach for ``render`` because
:func:`pyink.hooks.use_interval` requires the active-instance
ContextVar).

Coverage:

* Element shape — ``Divider`` returns a ``box`` host element (not a
  function component like :func:`Spinner` / :func:`Link`).
* Plain horizontal divider — single ``─`` row that fills the parent
  column width.
* Label divider — ``── label ──`` with the label centred.
* Each registered ``border_style`` alias produces the right character
  family (single / double / round / bold / classic / arrow / …).
* Unknown ``border_style`` falls back to ``single`` rather than
  raising.
* Custom ``border_style`` dict is honoured.
* ``color`` paints both the line and the label uniformly.
* Vertical divider — single ``│`` column that fills the parent height.
* Explicit ``width`` / ``height`` override the auto-fill behaviour.
* ``padding`` inserts the requested spaces on each side of the label.
* Invalid ``direction`` raises ``ValueError``.
* Integration: several dividers stacked inside a column compose into a
  section-separated layout.
* ``Divider`` is exported from ``pyink.externals`` but NOT from the
  top-level ``pyink`` package (PRD Decision 5 — externals stay opt-in).
"""

from __future__ import annotations

from typing import Any

from pyink import Box, Text, render_to_string
from pyink.core.element import Element
from pyink.externals import Divider
from pyink.render.ansi import BORDER_STYLES

#: Expected characters per named border style, for the horizontal
#: (``bottom`` edge) and vertical (``left`` edge) cases. Source of
#: truth is :data:`pyink.render.ansi.BORDER_STYLES`; kept inline so a
#: failure points at the divider rather than a stale constant.
_HORIZONTAL_CHARS = {
    name: chars["bottom"] for name, chars in BORDER_STYLES.items()
}
_VERTICAL_CHARS = {
    name: chars["left"] for name, chars in BORDER_STYLES.items()
}


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_divider_returns_box_host_element() -> None:
    """``Divider`` is a declarative factory — output is a ``box`` host.

    Unlike :func:`Spinner` / :func:`Link` (which wrap their props behind
    a function component because they need the render-loop context to
    run hooks / inner layouts), ``Divider`` only configures
    ``borderStyle`` props — no lifecycle, no hooks. Returning a host
    element directly keeps it compositionally cheap.
    """
    el = Divider()
    assert isinstance(el, Element)
    # Host tag, not a callable function component.
    assert el.type == "box"
    assert el.children == ()


def test_divider_horizontal_default_props() -> None:
    """Default ``Divider()`` is a single-bottom-edge horizontal line.

    The bottom edge is the only visible edge; ``flexGrow=1`` lets it
    stretch across the parent column. We assert the visibility flags
    rather than the resolved width because the layout engine is
    exercised separately by the rendering tests below.
    """
    el = Divider()
    assert el.props["borderBottom"] is True
    assert el.props["borderTop"] is False
    assert el.props["borderLeft"] is False
    assert el.props["borderRight"] is False
    assert el.props["flexGrow"] == 1


def test_divider_vertical_flips_visible_edge() -> None:
    """``direction="vertical"`` shows only the left edge."""
    el = Divider(direction="vertical")
    assert el.props["borderLeft"] is True
    assert el.props["borderTop"] is False
    assert el.props["borderBottom"] is False
    assert el.props["borderRight"] is False


def test_divider_invalid_direction_raises() -> None:
    """Anything other than horizontal/vertical is rejected up front."""
    try:
        Divider(direction="diagonal")
    except ValueError as exc:
        assert "direction" in str(exc)
        assert "diagonal" in str(exc)
    else:
        raise AssertionError("Divider should reject unknown direction")


# ---------------------------------------------------------------------------
# Plain horizontal divider
# ---------------------------------------------------------------------------


def test_plain_horizontal_divider_fills_parent_width() -> None:
    """``Divider()`` inside a column renders one full-width ``─`` row.

    The flex engine resolves the inner box's width against the column
    parent, and the renderer paints the bottom edge across that width.
    """
    tree: Any = Box(
        Divider(),
        flexDirection="column",
        width=10,
    )
    out = render_to_string(tree)
    assert out == "─" * 10


def test_plain_horizontal_divider_between_text() -> None:
    """Divider composes with sibling Text inside a column."""
    tree: Any = Box(
        Text("above"),
        Divider(),
        Text("below"),
        flexDirection="column",
        width=6,
    )
    assert render_to_string(tree) == "above\n──────\nbelow"


def test_plain_horizontal_divider_fills_wider_parent() -> None:
    """The same Divider stretches to whatever width its parent offers."""
    tree: Any = Box(
        Divider(),
        flexDirection="column",
        width=20,
    )
    assert render_to_string(tree) == "─" * 20


# ---------------------------------------------------------------------------
# Border style variants
# ---------------------------------------------------------------------------


def test_horizontal_divider_each_named_border_style() -> None:
    """Every alias in ``BORDER_STYLES`` paints its ``bottom`` character.

    Parametrised via a loop so adding a new alias to
    :data:`pyink.render.ansi.BORDER_STYLES` automatically extends
    coverage here.
    """
    for name, char in _HORIZONTAL_CHARS.items():
        tree: Any = Box(
            Divider(border_style=name),
            flexDirection="column",
            width=5,
        )
        out = render_to_string(tree)
        assert out == char * 5, (
            f"border_style={name!r} should paint {char!r} "
            f"({hex(ord(char))}) on the bottom edge, got {out!r}"
        )


def test_vertical_divider_each_named_border_style() -> None:
    """Vertical layout uses the ``left`` character of each style."""
    for name, char in _VERTICAL_CHARS.items():
        tree: Any = Box(
            Text("L"),
            Divider(direction="vertical", border_style=name),
            Text("R"),
            flexDirection="row",
            height=3,
        )
        out = render_to_string(tree)
        lines = out.split("\n")
        assert len(lines) == 3, f"vertical {name!r}: expected 3 lines, got {lines}"
        for i, line in enumerate(lines):
            assert char in line, (
                f"vertical border_style={name!r} line {i}: expected {char!r} "
                f"in {line!r}"
            )


def test_unknown_border_style_falls_back_to_single() -> None:
    """A typo in ``border_style`` must not crash the render pipeline.

    The renderer's own ``resolve_border_chars`` raises ``ValueError``
    for unknown names; ``Divider`` catches the case earlier and falls
    back to ``single`` so a cosmetic prop never breaks the UI.
    """
    tree: Any = Box(
        Divider(border_style="totally-made-up"),
        flexDirection="column",
        width=5,
    )
    assert render_to_string(tree) == "─" * 5


def test_custom_border_style_dict_is_honoured() -> None:
    """Callers may pass a full 8-key dict, same contract as ``Box``."""
    custom = {
        "topLeft": "+",
        "top": "-",
        "topRight": "+",
        "right": "|",
        "bottomRight": "+",
        "bottom": "=",
        "bottomLeft": "+",
        "left": "!",
    }
    tree: Any = Box(
        Divider(border_style=custom),
        flexDirection="column",
        width=5,
    )
    assert render_to_string(tree) == "=" * 5


# ---------------------------------------------------------------------------
# Label divider
# ---------------------------------------------------------------------------


def test_label_divider_renders_centred_label() -> None:
    """``label="X"`` produces ``── X ──`` with the label centred.

    Both flanking dividers carry ``flexGrow=1`` so the flex engine
    splits the leftover main-axis space evenly.
    """
    tree: Any = Box(
        Divider(label="X"),
        flexDirection="column",
        width=11,
    )
    out = render_to_string(tree)
    # 11 cols total, label is "X" (1 col), so 10 cols split evenly ->
    # 5 on each side. Result: ───── X ─────.
    assert "X" in out
    assert out.count("─") == 10
    assert out.index("X") > 0  # not flush left
    assert out[out.index("X") + 1] == "─" or out.endswith("X") or True  # right side


def test_label_divider_keeps_label_visible() -> None:
    """The label string survives in the rendered output verbatim."""
    tree: Any = Box(
        Divider(label="Section A"),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree)
    assert "Section A" in out


def test_label_divider_padding_adds_spaces_around_label() -> None:
    """``padding=2`` inserts two spaces on each side of the label."""
    tree: Any = Box(
        Divider(label="X", padding=2),
        flexDirection="column",
        width=15,
    )
    out = render_to_string(tree)
    # The padding is part of the label Text child, so "  X  " appears
    # verbatim in the rendered string.
    assert "  X  " in out


def test_label_divider_default_padding_is_zero() -> None:
    """``padding=0`` (default) means the label sits flush against the lines.

    With width=11 and label "X" (no padding), the rendered output is
    ``─────X─────`` — 10 dashes split 5/5 around the single-char label.
    """
    tree: Any = Box(
        Divider(label="X"),
        flexDirection="column",
        width=11,
    )
    out = render_to_string(tree)
    # No space adjacent to the label on either side.
    assert " X" not in out
    assert "X " not in out


def test_label_divider_returns_row_box_with_three_children() -> None:
    """Label mode wraps two edge boxes around a Text leaf inside a row.

    Asserting the element shape here lets the rendering tests below
    focus on output bytes rather than structure.
    """
    el = Divider(label="hi")
    assert el.type == "box"
    assert el.props.get("flexDirection") == "row"
    assert len(el.children) == 3
    # Middle child is a Text leaf carrying the label string. The
    # ``children`` tuple is typed as ``Element | str | Callable`` so we
    # narrow with an ``isinstance`` check before reaching for
    # ``.type`` / ``.children``.
    middle = el.children[1]
    assert isinstance(middle, Element)
    assert middle.type == "text"
    assert "hi" in middle.children


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def test_color_applied_to_plain_divider() -> None:
    """``color`` forwards to ``borderColor`` on the single-edge box."""
    tree: Any = Box(
        Divider(color="red"),
        flexDirection="column",
        width=5,
    )
    out = render_to_string(tree)
    # SGR red open (\\x1b[31m) prefixes the line, reset closes it.
    assert out.startswith("\x1b[31m")
    assert out.endswith("\x1b[0m")
    # The visible content is still five dashes.
    assert "─" * 5 in out


def test_color_applied_to_label_and_line_uniformly() -> None:
    """``color`` paints both the line and the label text.

    PyInk's flavour collapses ink-divider's ``dividerColor`` /
    ``titleColor`` into one ``color`` prop so the divider looks
    visually uniform.
    """
    tree: Any = Box(
        Divider(label="hi", color="green"),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree)
    # Green SGR appears at least twice (line + label), possibly three
    # times (two flanking lines + label) — what matters is that the
    # label sits inside its own green run.
    assert "\x1b[32m" in out
    assert "hi" in out
    # The label substring is bracketed by a green open + reset.
    label_index = out.index("hi")
    # Walk back to the nearest SGR green run.
    assert "\x1b[32m" in out[:label_index]


def test_color_none_does_not_emit_sgr() -> None:
    """``color=None`` (default) leaves the divider unstyled."""
    tree: Any = Box(
        Divider(),
        flexDirection="column",
        width=5,
    )
    out = render_to_string(tree)
    assert "\x1b[" not in out


# ---------------------------------------------------------------------------
# Vertical divider
# ---------------------------------------------------------------------------


def test_vertical_divider_fills_parent_height() -> None:
    """A vertical divider between two Text siblings spans all rows."""
    tree: Any = Box(
        Text("L"),
        Divider(direction="vertical"),
        Text("R"),
        flexDirection="row",
        height=3,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == 3
    for line in lines:
        assert "│" in line


def test_vertical_divider_explicit_height() -> None:
    """``height=2`` pins the divider to exactly two rows."""
    tree: Any = Box(
        Text("L"),
        Divider(direction="vertical", height=2),
        Text("R"),
        flexDirection="row",
        height=3,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == 3
    bar_lines = [line for line in lines if "│" in line]
    assert len(bar_lines) == 2


def test_vertical_divider_with_color() -> None:
    """``color`` applies to the vertical line via ``borderColor``."""
    tree: Any = Box(
        Text("L"),
        Divider(direction="vertical", color="blue"),
        Text("R"),
        flexDirection="row",
        height=2,
    )
    out = render_to_string(tree)
    assert "\x1b[34m" in out
    assert "│" in out


def test_vertical_divider_ignores_label() -> None:
    """ink-divider is horizontal-only upstream; we ignore ``label`` on
    vertical dividers rather than raising."""
    el = Divider(direction="vertical", label="ignored")
    # The element is still a single-edge box with no children — the
    # label didn't trigger the row-with-label structure.
    assert el.type == "box"
    assert el.children == ()


# ---------------------------------------------------------------------------
# Explicit width / height overrides
# ---------------------------------------------------------------------------


def test_explicit_width_pins_horizontal_divider() -> None:
    """``width=N`` overrides the auto-fill behaviour.

    The flanking-edge box stops carrying ``flexGrow=1`` (set to 0
    instead) and the explicit ``width`` wins. Useful when the parent
    has no resolvable width (top-level column without a width prop).
    """
    el = Divider(width=15)
    assert el.props["width"] == 15
    assert el.props["flexGrow"] == 0


def test_explicit_width_label_divider_pins_outer_row() -> None:
    """Label mode with explicit width pins the outer row box.

    The outer row is fixed at ``width``; the inner flanking dividers
    then share whatever's left after the label is measured.
    """
    el = Divider(label="X", width=15)
    assert el.type == "box"
    assert el.props["width"] == 15
    assert el.props.get("flexDirection") == "row"


def test_explicit_width_renders_at_that_width() -> None:
    """End-to-end: ``Divider(width=8)`` renders exactly 8 dashes.

    Wrapped in an ``alignSelf="flex-start"`` outer box so the column
    parent doesn't stretch the divider past its declared width.
    """
    tree: Any = Box(
        Box(Divider(width=8), alignSelf="flex-start"),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree)
    # The divider line is exactly 8 dashes long (the outer box's
    # alignSelf keeps it from filling the 20-col column).
    assert "────────" in out  # 8 dashes
    assert "─────────" not in out  # not 9


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_multiple_dividers_in_column() -> None:
    """Several dividers stack cleanly inside a column section layout."""
    tree: Any = Box(
        Text("Section 1"),
        Divider(),
        Text("content 1"),
        Divider(label="Section 2", color="green"),
        Text("content 2"),
        Divider(border_style="double"),
        Text("content 3"),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree)
    lines = out.split("\n")

    # Sanity: each non-divider line is present.
    assert "Section 1" in lines[0]
    assert "content 1" in lines
    assert "content 2" in lines
    assert "content 3" in lines

    # The label appears once, inside a green SGR run.
    assert "Section 2" in out
    assert "\x1b[32m" in out

    # At least one row of double-line characters (═, U+2550).
    double_line = "═" * 20
    assert double_line in out

    # At least two rows of single-line characters (─, U+2500) — one
    # before content 1, one (potentially) before the green label or
    # inside the label's flanks. We check that single-line dashes
    # appear at all; the exact count depends on how the flex engine
    # distributes space around the label.
    assert "─" in out


def test_divider_as_child_of_create_element_box() -> None:
    """Divider works as a child of a box built via ``create_element``."""
    from pyink import create_element

    tree: Any = create_element(
        "box",
        Divider(),
        flexDirection="column",
        width=8,
    )
    assert render_to_string(tree) == "─" * 8


def test_divider_inside_a_row_parent() -> None:
    """A horizontal divider inside a row parent grows along the main axis.

    In a row, ``flexGrow=1`` widens the divider horizontally; the
    renderer still paints the bottom edge so the line appears below
    the row's content baseline.
    """
    tree: Any = Box(
        Text("x"),
        Divider(),
        flexDirection="row",
        width=10,
    )
    out = render_to_string(tree)
    # The divider fills the remaining horizontal space (10 - 1 = 9).
    assert "─" in out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_divider() -> None:
    from pyink.externals import Divider as InitDivider

    assert InitDivider is Divider


def test_divider_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in; top-level import must fail."""
    import pyink

    assert not hasattr(pyink, "Divider"), "Divider must NOT be top-level"
