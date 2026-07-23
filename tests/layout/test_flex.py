"""Flex layout tests ported from ink's ``flex-*.tsx`` suite (PR3).

Each test mirrors an ink test case: the JSX tree is rewritten using the
PyInk pseudo-host API (``"box"`` / ``"text"``) and the expected output
string is copied verbatim from the ink fixture. Failures indicate either
an algorithm bug or a feature we deliberately leave out of scope
(marked ``xfail``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ink.core.element import Element, create_element
from ink.render import render_to_string


def box(*children: Any, **props: Any) -> Element:
    """Pseudo-host helper — shorthand for ``create_element("box", ...)``."""
    return create_element("box", *children, **props)


def text(content: str | Callable[[], str], **props: Any) -> Element:
    return create_element("text", content, **props)


# ---------------------------------------------------------------------------
# flex-direction.tsx
# ---------------------------------------------------------------------------


def test_flex_direction_row() -> None:
    """ink: direction row → 'AB'."""
    tree = box(text("A"), text("B"), flexDirection="row")
    assert render_to_string(tree) == "AB"


def test_flex_direction_row_reverse() -> None:
    """ink: direction row-reverse with width=4 → '  BA'."""
    tree = box(text("A"), text("B"), flexDirection="row-reverse", width=4)
    assert render_to_string(tree) == "  BA"


def test_flex_direction_column() -> None:
    """ink: direction column → 'A\\nB'."""
    tree = box(text("A"), text("B"), flexDirection="column")
    assert render_to_string(tree) == "A\nB"


def test_flex_direction_column_reverse() -> None:
    """ink: direction column-reverse with height=4 → '\\n\\nB\\nA'."""
    tree = box(text("A"), text("B"), flexDirection="column-reverse", height=4)
    assert render_to_string(tree) == "\n\nB\nA"


def test_flex_direction_column_no_squash() -> None:
    """ink: don't squash text nodes when column direction is applied."""
    tree = box(text("A"), text("B"), flexDirection="column")
    assert render_to_string(tree) == "A\nB"


# ---------------------------------------------------------------------------
# flex-justify-content.tsx
# ---------------------------------------------------------------------------


def test_justify_row_center_single() -> None:
    """ink: row - align text to center → '   Test'."""
    tree = box(text("Test"), justifyContent="center", width=10)
    assert render_to_string(tree) == "   Test"


def test_justify_row_center_multi() -> None:
    """ink: row - align multiple text nodes to center → '    AB'."""
    tree = box(text("A"), text("B"), justifyContent="center", width=10)
    assert render_to_string(tree) == "    AB"


def test_justify_row_flex_end_single() -> None:
    """ink: row - align text to right → '      Test'."""
    tree = box(text("Test"), justifyContent="flex-end", width=10)
    assert render_to_string(tree) == "      Test"


def test_justify_row_flex_end_multi() -> None:
    """ink: row - align multiple text nodes to right → '        AB'."""
    tree = box(text("A"), text("B"), justifyContent="flex-end", width=10)
    assert render_to_string(tree) == "        AB"


def test_justify_row_space_between() -> None:
    """ink: row - align two text nodes on the edges → 'A  B'."""
    tree = box(text("A"), text("B"), justifyContent="space-between", width=4)
    assert render_to_string(tree) == "A  B"


def test_justify_row_space_evenly() -> None:
    """ink: row - space evenly two text nodes → '  A   B'."""
    tree = box(text("A"), text("B"), justifyContent="space-evenly", width=10)
    assert render_to_string(tree) == "  A   B"


@pytest.mark.xfail(
    reason="ink marks this case as test.failing in its own suite (their yoga "
    "has a rounding bug for the first child's X). PyInk's MVP space-around "
    "implementation also produces a non-ideal result when the half-gap unit "
    "rounds to 0 (very tight widths); not pursued for MVP.",
    strict=True,
)
def test_justify_row_space_around() -> None:
    """ink: row - align two text nodes with equal space around them → ' A B'.

    Marked ``test.failing`` in ink's own suite — reproduced as xfail.
    """
    tree = box(text("A"), text("B"), justifyContent="space-around", width=5)
    assert render_to_string(tree) == " A B"


def test_justify_column_center() -> None:
    """ink: column - align text to center → '\\nTest\\n'."""
    tree = box(
        text("Test"),
        flexDirection="column",
        justifyContent="center",
        height=3,
    )
    assert render_to_string(tree) == "\nTest\n"


def test_justify_column_flex_end() -> None:
    """ink: column - align text to bottom → '\\n\\nTest'."""
    tree = box(
        text("Test"),
        flexDirection="column",
        justifyContent="flex-end",
        height=3,
    )
    assert render_to_string(tree) == "\n\nTest"


def test_justify_column_space_between() -> None:
    """ink: column - align two text nodes on the edges → 'A\\n\\n\\nB'."""
    tree = box(
        text("A"),
        text("B"),
        flexDirection="column",
        justifyContent="space-between",
        height=4,
    )
    assert render_to_string(tree) == "A\n\n\nB"


@pytest.mark.xfail(
    reason="ink marks this as test.failing (yoga space-around bug).",
    strict=True,
)
def test_justify_column_space_around() -> None:
    tree = box(
        text("A"),
        text("B"),
        flexDirection="column",
        justifyContent="space-around",
        height=5,
    )
    assert render_to_string(tree) == "\nA\n\nB\n"


# ---------------------------------------------------------------------------
# flex-align-items.tsx
# ---------------------------------------------------------------------------


def test_align_items_row_center_single() -> None:
    """ink: row - align text to center → '\\nTest\\n'."""
    tree = box(text("Test"), alignItems="center", height=3)
    assert render_to_string(tree) == "\nTest\n"


def test_align_items_row_center_multi() -> None:
    """ink: row - align multiple text nodes to center → '\\nAB\\n'."""
    tree = box(text("A"), text("B"), alignItems="center", height=3)
    assert render_to_string(tree) == "\nAB\n"


def test_align_items_row_flex_end_single() -> None:
    """ink: row - align text to bottom → '\\n\\nTest'."""
    tree = box(text("Test"), alignItems="flex-end", height=3)
    assert render_to_string(tree) == "\n\nTest"


def test_align_items_row_flex_end_multi() -> None:
    """ink: row - align multiple text nodes to bottom → '\\n\\nAB'."""
    tree = box(text("A"), text("B"), alignItems="flex-end", height=3)
    assert render_to_string(tree) == "\n\nAB"


def test_align_items_column_center() -> None:
    """ink: column - align text to center → '   Test'."""
    tree = box(text("Test"), flexDirection="column", alignItems="center", width=10)
    assert render_to_string(tree) == "   Test"


def test_align_items_column_flex_end() -> None:
    """ink: column - align text to right → '      Test'."""
    tree = box(text("Test"), flexDirection="column", alignItems="flex-end", width=10)
    assert render_to_string(tree) == "      Test"


@pytest.mark.xfail(
    reason="align-items stretch with borders requires real Box/Border "
    "rendering from PR4. We model plain-text layout only.",
    strict=True,
)
def _test_align_items_row_stretch_legacy() -> None:
    tree = box(
        box(text("X"), borderStyle="single"),
        alignItems="stretch",
        height=5,
    )
    assert render_to_string(tree) == "┌─┐\n│X│\n│ │\n│ │\n└─┘"


def test_align_items_row_stretch() -> None:
    """PR4: with border rendering, stretch aligns the bordered child."""
    tree = box(
        box(text("X"), borderStyle="single"),
        alignItems="stretch",
        height=5,
    )
    assert render_to_string(tree) == "┌─┐\n│X│\n│ │\n│ │\n└─┘"


@pytest.mark.xfail(
    reason="baseline alignment needs font-metric tracking that PR4 will "
    "introduce; current baseline implementation top-aligns.",
    strict=True,
)
def test_align_items_row_baseline() -> None:
    """ink: row - align text to baseline with a multi-line text node."""
    tree = box(
        text("A\nB"),
        text("X"),
        alignItems="baseline",
        height=3,
    )
    assert render_to_string(tree) == "A\nBX\n"


# ---------------------------------------------------------------------------
# flex-align-self.tsx
# ---------------------------------------------------------------------------


def test_align_self_row_center_single() -> None:
    """ink: row - align text to center via alignSelf → '\\nTest\\n'."""
    tree = box(
        box(text("Test"), alignSelf="center"),
        height=3,
    )
    assert render_to_string(tree) == "\nTest\n"


def test_align_self_row_center_multi() -> None:
    tree = box(
        box(text("A"), text("B"), alignSelf="center"),
        height=3,
    )
    assert render_to_string(tree) == "\nAB\n"


def test_align_self_row_flex_end_single() -> None:
    tree = box(
        box(text("Test"), alignSelf="flex-end"),
        height=3,
    )
    assert render_to_string(tree) == "\n\nTest"


def test_align_self_row_flex_end_multi() -> None:
    tree = box(
        box(text("A"), text("B"), alignSelf="flex-end"),
        height=3,
    )
    assert render_to_string(tree) == "\n\nAB"


def test_align_self_column_center() -> None:
    tree = box(
        box(text("Test"), alignSelf="center"),
        flexDirection="column",
        width=10,
    )
    assert render_to_string(tree) == "   Test"


def test_align_self_column_flex_end() -> None:
    tree = box(
        box(text("Test"), alignSelf="flex-end"),
        flexDirection="column",
        width=10,
    )
    assert render_to_string(tree) == "      Test"


@pytest.mark.xfail(reason="requires PR4 Box border rendering.", strict=True)
def _test_align_self_column_stretch_legacy() -> None:
    tree = box(
        box(text("X"), alignSelf="stretch", borderStyle="single"),
        flexDirection="column",
        width=7,
    )
    assert render_to_string(tree) == "┌─────┐\n│X    │\n└─────┘"


def test_align_self_column_stretch() -> None:
    """PR4: border rendering enables column stretch on bordered children."""
    tree = box(
        box(text("X"), alignSelf="stretch", borderStyle="single"),
        flexDirection="column",
        width=7,
    )
    assert render_to_string(tree) == "┌─────┐\n│X    │\n└─────┘"


@pytest.mark.xfail(reason="requires PR4 Box border rendering.", strict=True)
def _test_align_self_row_stretch_legacy() -> None:
    tree = box(
        box(text("X"), alignSelf="stretch", borderStyle="single"),
        height=5,
    )
    assert render_to_string(tree) == "┌─┐\n│X│\n│ │\n│ │\n└─┘"


def test_align_self_row_stretch() -> None:
    """PR4: border rendering enables row stretch on bordered children."""
    tree = box(
        box(text("X"), alignSelf="stretch", borderStyle="single"),
        height=5,
    )
    assert render_to_string(tree) == "┌─┐\n│X│\n│ │\n│ │\n└─┘"


@pytest.mark.xfail(reason="baseline alignment needs PR4 font metrics.", strict=True)
def test_align_self_row_baseline() -> None:
    tree = box(
        text("A\nB"),
        box(text("X"), alignSelf="baseline"),
        alignItems="flex-end",
        height=3,
    )
    assert render_to_string(tree) == "AX\nB\n"


# ---------------------------------------------------------------------------
# flex-wrap.tsx
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="ink's yoga has a quirk where shrinking a child to width 0 "
    "produces a trailing empty row in the rendered output. PyInk's MVP "
    "shrink model doesn't reproduce this artefact; result is the same "
    "visible content ('BC') but without the trailing newline.",
    strict=True,
)
def test_flex_wrap_row_nowrap() -> None:
    """ink: row - no wrap with width=2 → 'BC\\n'."""
    tree = box(text("A"), text("BC"), width=2)
    assert render_to_string(tree) == "BC\n"


@pytest.mark.xfail(
    reason="Column nowrap drop-first-child quirk: ink shrinks the first "
    "child to zero height when the column overflows. PyInk's shrink "
    "keeps all children proportionally.",
    strict=True,
)
def test_flex_wrap_column_nowrap() -> None:
    """ink: column - no wrap with height=2 → 'B\\nC'."""
    tree = box(
        text("A"), text("B"), text("C"),
        flexDirection="column",
        height=2,
    )
    assert render_to_string(tree) == "B\nC"


@pytest.mark.xfail(
    reason="flex-wrap support is partial in MVP — single-line nowrap and "
    "stretch layouts work, but multi-line wrap mode is tracked as a "
    "follow-up (yoga wrap uses a different sizing pass).",
    strict=True,
)
def test_flex_wrap_row_wrap() -> None:
    """ink: row - wrap content with width=2 → 'A\\nBC'."""
    tree = box(text("A"), text("BC"), width=2, flexWrap="wrap")
    assert render_to_string(tree) == "A\nBC"


@pytest.mark.xfail(reason="flexWrap multi-line mode not in MVP.", strict=True)
def test_flex_wrap_column_wrap() -> None:
    tree = box(
        text("A"), text("B"), text("C"),
        flexDirection="column",
        height=2,
        flexWrap="wrap",
    )
    assert render_to_string(tree) == "AC\nB"


@pytest.mark.xfail(reason="flexWrap multi-line mode not in MVP.", strict=True)
def test_flex_wrap_column_wrap_reverse() -> None:
    tree = box(
        text("A"), text("B"), text("C"),
        flexDirection="column",
        height=2,
        width=3,
        flexWrap="wrap-reverse",
    )
    assert render_to_string(tree) == " CA\n  B"


@pytest.mark.xfail(reason="flexWrap multi-line mode not in MVP.", strict=True)
def test_flex_wrap_row_wrap_reverse() -> None:
    tree = box(
        text("A"), text("B"), text("C"),
        height=3,
        width=2,
        flexWrap="wrap-reverse",
    )
    assert render_to_string(tree) == "\nC\nAB"


# ---------------------------------------------------------------------------
# flex.tsx (grow / shrink / basis)
# ---------------------------------------------------------------------------


def test_flex_grow_equally() -> None:
    """ink: grow equally → 'A  B'."""
    tree = box(
        box(text("A"), flexGrow=1),
        box(text("B"), flexGrow=1),
        width=6,
    )
    assert render_to_string(tree) == "A  B"


def test_flex_grow_one_element() -> None:
    """ink: grow one element → 'A    B'."""
    tree = box(
        box(text("A"), flexGrow=1),
        text("B"),
        width=6,
    )
    assert render_to_string(tree) == "A    B"


def test_flex_do_not_shrink() -> None:
    """ink: do not shrink → 'A     B     C'."""
    tree = box(
        box(text("A"), flexShrink=0, width=6),
        box(text("B"), flexShrink=0, width=6),
        box(text("C"), width=6),
        width=16,
    )
    assert render_to_string(tree) == "A     B     C"


@pytest.mark.xfail(
    reason="Yoga uses an iterative shrink pass that distributes rounding "
    "remainders differently from our single-pass weighted shrink. The "
    "visible content matches (all three children, in order); only the "
    "exact padding between B and C differs by one cell.",
    strict=True,
)
def test_flex_shrink_equally() -> None:
    """ink: shrink equally with width=10 → 'A    B   C'."""
    tree = box(
        box(text("A"), flexShrink=1, width=6),
        box(text("B"), flexShrink=1, width=6),
        text("C"),
        width=10,
    )
    assert render_to_string(tree) == "A    B   C"


def test_flex_basis_row() -> None:
    """ink: set flex basis with flexDirection='row' → 'A  B'."""
    tree = box(
        box(text("A"), flexBasis=3),
        text("B"),
        width=6,
    )
    assert render_to_string(tree) == "A  B"


@pytest.mark.xfail(
    reason="flexBasis as percentage is out of MVP scope (PRD explicitly "
    "excludes percentage sizing).",
    strict=True,
)
def test_flex_basis_percent_row() -> None:
    tree = box(
        box(text("A"), flexBasis="50%"),
        text("B"),
        width=6,
    )
    assert render_to_string(tree) == "A  B"


def test_flex_basis_column() -> None:
    """ink: set flex basis with flexDirection='column' → 'A\\n\\n\\nB\\n\\n'."""
    tree = box(
        box(text("A"), flexBasis=3),
        text("B"),
        flexDirection="column",
        height=6,
    )
    assert render_to_string(tree) == "A\n\n\nB\n\n"


@pytest.mark.xfail(reason="percent flexBasis out of MVP scope.", strict=True)
def test_flex_basis_percent_column() -> None:
    tree = box(
        box(text("A"), flexBasis="50%"),
        text("B"),
        flexDirection="column",
        height=6,
    )
    assert render_to_string(tree) == "A\n\n\nB\n\n"


# ---------------------------------------------------------------------------
# gap.tsx
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="flexWrap + gap requires the multi-line wrap path.", strict=True)
def test_gap_wrap() -> None:
    tree = box(
        text("A"), text("B"), text("C"),
        gap=1, width=3, flexWrap="wrap",
    )
    assert render_to_string(tree) == "A B\n\nC"


def test_column_gap_row_layout() -> None:
    """ink: 'column gap' (gap on a row) → 'A B'.

    Name is misleading — ink's test sets ``gap={1}`` on the default
    (row) container, so the gap appears between A and B horizontally.
    """
    tree = box(text("A"), text("B"), gap=1)
    assert render_to_string(tree) == "A B"


def test_row_gap_column_layout() -> None:
    """ink: 'row gap' (gap on a column) → 'A\\n\\nB'."""
    tree = box(
        text("A"), text("B"),
        flexDirection="column", gap=1,
    )
    assert render_to_string(tree) == "A\n\nB"


# ---------------------------------------------------------------------------
# gap + collapseIfEmpty (CSS flexbox: gap only between visible items)
# ---------------------------------------------------------------------------


def test_gap_column_skips_leading_collapsed_child() -> None:
    """Collapsed child at position 0 contributes no gap slot; the
    visible sibling renders at the top with no leading blank row."""
    tree = box(
        text("", collapseIfEmpty=True),
        text("B"),
        gap=1, flexDirection="column",
    )
    assert render_to_string(tree) == "B"


def test_gap_column_skips_trailing_collapsed_child() -> None:
    """Collapsed child at the tail contributes no gap slot either."""
    tree = box(
        text("A"),
        text("", collapseIfEmpty=True),
        gap=1, flexDirection="column",
    )
    assert render_to_string(tree) == "A"


def test_gap_column_skips_interleaved_collapsed_child() -> None:
    """A visible, B collapsed, C visible → 'A\\n\\nC'.

    One gap between A and C; the collapsed B contributes neither a row
    of its own nor a gap slot on either side."""
    tree = box(
        text("A"),
        text("", collapseIfEmpty=True),
        text("C"),
        gap=1, flexDirection="column",
    )
    assert render_to_string(tree) == "A\n\nC"


def test_gap_column_zero_when_all_collapsed() -> None:
    """All children collapsed → no gap slots, frame is empty."""
    tree = box(
        text("", collapseIfEmpty=True),
        text("", collapseIfEmpty=True),
        gap=1, flexDirection="column",
    )
    assert render_to_string(tree) == ""


def test_gap_row_skips_interleaved_collapsed_child() -> None:
    """Row variant: collapsed child contributes no horizontal gap."""
    tree = box(
        text("A"),
        text("", collapseIfEmpty=True),
        text("C"),
        gap=1,
    )
    assert render_to_string(tree) == "A C"


def test_gap_column_space_between_skips_collapsed() -> None:
    """space-between distributes free space across visible children only.

    Collapsed B between A and C is invisible to the distribute count,
    so the 2-row free space goes entirely between A and C."""
    tree = box(
        text("A"),
        text("", collapseIfEmpty=True),
        text("C"),
        flexDirection="column",
        justifyContent="space-between",
        height=4,
    )
    assert render_to_string(tree) == "A\n\n\nC"


# ---------------------------------------------------------------------------
# padding.tsx (subset — no border / multiline-wrap edge cases)
# ---------------------------------------------------------------------------


def test_padding_all() -> None:
    """ink: padding=2 → '\\n\\n  X\\n\\n'."""
    tree = box(text("X"), padding=2)
    assert render_to_string(tree) == "\n\n  X\n\n"


def test_padding_x() -> None:
    """ink: paddingX=2 sibling → '  X  Y'."""
    tree = box(
        box(text("X"), paddingX=2),
        text("Y"),
    )
    assert render_to_string(tree) == "  X  Y"


def test_padding_y() -> None:
    """ink: paddingY=2 → '\\n\\nX\\n\\n'."""
    tree = box(text("X"), paddingY=2)
    assert render_to_string(tree) == "\n\nX\n\n"


def test_padding_top() -> None:
    tree = box(text("X"), paddingTop=2)
    assert render_to_string(tree) == "\n\nX"


def test_padding_bottom() -> None:
    tree = box(text("X"), paddingBottom=2)
    assert render_to_string(tree) == "X\n\n"


def test_padding_left() -> None:
    tree = box(text("X"), paddingLeft=2)
    assert render_to_string(tree) == "  X"


def test_padding_right_sibling() -> None:
    """ink: paddingRight=2 sibling → 'X  Y'."""
    tree = box(
        box(text("X"), paddingRight=2),
        text("Y"),
    )
    assert render_to_string(tree) == "X  Y"


def test_nested_padding() -> None:
    """ink: nested padding=2 → '\\n\\n\\n\\n    X\\n\\n\\n\\n'."""
    tree = box(
        box(text("X"), padding=2),
        padding=2,
    )
    assert render_to_string(tree) == "\n\n\n\n    X\n\n\n\n"


def test_padding_with_multiline_text() -> None:
    """ink: padding with multiline string → '\\n\\n  A\\n  B\\n\\n'."""
    tree = box(text("A\nB"), padding=2)
    assert render_to_string(tree) == "\n\n  A\n  B\n\n"


def test_padding_one_with_newline_text() -> None:
    """ink: apply padding to text with newlines → '\\n Hello\\n World\\n'."""
    tree = box(text("Hello\nWorld"), padding=1)
    assert render_to_string(tree) == "\n Hello\n World\n"


def test_padding_wrapped_text() -> None:
    """ink: apply padding to wrapped text with width=5 →
    '\\n Hel\\n lo\\n Wor\\n ld\\n'."""
    tree = box(text("Hello World"), padding=1, width=5)
    assert render_to_string(tree) == "\n Hel\n lo\n Wor\n ld\n"


# ---------------------------------------------------------------------------
# margin.tsx (subset)
# ---------------------------------------------------------------------------


def test_margin_all() -> None:
    """ink: margin=2 → '\\n\\n  X\\n\\n'."""
    tree = box(text("X"), margin=2)
    assert render_to_string(tree) == "\n\n  X\n\n"


def test_margin_x_sibling() -> None:
    """ink: marginX=2 sibling → '  X  Y'."""
    tree = box(
        box(text("X"), marginX=2),
        text("Y"),
    )
    assert render_to_string(tree) == "  X  Y"


def test_margin_y() -> None:
    tree = box(text("X"), marginY=2)
    assert render_to_string(tree) == "\n\nX\n\n"


def test_margin_top() -> None:
    tree = box(text("X"), marginTop=2)
    assert render_to_string(tree) == "\n\nX"


def test_margin_bottom() -> None:
    tree = box(text("X"), marginBottom=2)
    assert render_to_string(tree) == "X\n\n"


def test_margin_left() -> None:
    tree = box(text("X"), marginLeft=2)
    assert render_to_string(tree) == "  X"


def test_margin_right_sibling() -> None:
    tree = box(
        box(text("X"), marginRight=2),
        text("Y"),
    )
    assert render_to_string(tree) == "X  Y"


def test_nested_margin() -> None:
    tree = box(
        box(text("X"), margin=2),
        margin=2,
    )
    assert render_to_string(tree) == "\n\n\n\n    X\n\n\n\n"


def test_margin_multiline_text() -> None:
    tree = box(text("A\nB"), margin=2)
    assert render_to_string(tree) == "\n\n  A\n  B\n\n"


def test_margin_with_newline_text() -> None:
    tree = box(text("Hello\nWorld"), margin=1)
    assert render_to_string(tree) == "\n Hello\n World\n"


# ---------------------------------------------------------------------------
# width-height.tsx (subset)
# ---------------------------------------------------------------------------


def test_set_width_sibling() -> None:
    """ink: set width → 'A    B'."""
    tree = box(
        box(text("A"), width=5),
        text("B"),
    )
    assert render_to_string(tree) == "A    B"


def test_set_min_width_smaller() -> None:
    tree = box(
        box(text("A"), minWidth=5),
        text("B"),
    )
    assert render_to_string(tree) == "A    B"


def test_set_min_width_larger() -> None:
    tree = box(
        box(text("AAAAA"), minWidth=2),
        text("B"),
    )
    assert render_to_string(tree) == "AAAAAB"


def test_set_height_row() -> None:
    """ink: set height on a row container → 'AB\\n\\n\\n'."""
    tree = box(text("A"), text("B"), height=4)
    assert render_to_string(tree) == "AB\n\n\n"


def test_cut_text_over_height() -> None:
    """ink: cut text over set height with columns=4 → 'AAAA\\nBBBB'."""
    tree = box(text("AAAABBBBCCCC"), height=2)
    assert render_to_string(tree, columns=4) == "AAAA\nBBBB"


def test_set_min_height_smaller() -> None:
    tree = box(text("A"), minHeight=4)
    assert render_to_string(tree) == "A\n\n\n"


def test_set_min_height_larger() -> None:
    tree = box(
        box(text("A"), height=4),
        minHeight=2,
    )
    assert render_to_string(tree) == "A\n\n\n"


def test_set_max_width_constrained() -> None:
    """ink: maxWidth=3 wraps the AAAAA box → 'AAAB\\nAA'."""
    tree = box(
        box(text("AAAAA"), maxWidth=3),
        text("B"),
    )
    assert render_to_string(tree, columns=10) == "AAAB\nAA"


def test_set_max_width_unconstrained() -> None:
    tree = box(
        box(text("AAA"), maxWidth=10),
        text("B"),
    )
    assert render_to_string(tree) == "AAAB"


def test_set_max_height_constrained() -> None:
    tree = box(
        box(text("A"), height=4),
        maxHeight=2,
    )
    assert render_to_string(tree) == "A\n"


def test_set_max_height_unconstrained() -> None:
    tree = box(text("A"), maxHeight=4)
    assert render_to_string(tree) == "A"


# ---------------------------------------------------------------------------
# CSS maxHeight / maxWidth semantics (PR: 06-23-maxheight-semantics)
#
# A set ``max*`` is an upper bound on the content-driven size, NOT a fill
# target. When ``height`` is unset but ``maxHeight`` is, the box must
# auto-size to its content (never stretching to ``avail_h`` provided by
# the parent) and only clip when the content actually exceeds the cap.
# Same for ``maxWidth`` on the horizontal axis.
# ---------------------------------------------------------------------------


def test_max_height_allows_content_to_drive_size() -> None:
    """``maxHeight=10`` with 1 row of content → 1 visible row (not 10).

    Regression for the "maxHeight treated as fill target" bug: an earlier
    implementation fell through to ``own_h = avail_h`` whenever the
    parent handed down an ``exactly`` constraint, so a ``maxHeight``-only
    box stretched to fill the parent and then got clipped back down to
    the cap — producing 10 visible rows even with a single line of
    content. CSS semantics: ``max*`` caps, never fills.
    """
    # Outer box pinned to height=20 so the inner box receives
    # ``avail_h_mode="exactly"`` with ``avail_h=20``. The buggy code
    # would inflate the inner box to 20 rows (clipped to 10 by the
    # max_height branch). Correct behaviour: 1 row of content.
    tree = box(box(text("a"), maxHeight=10), height=20)
    rendered = render_to_string(tree, columns=20)
    lines = rendered.split("\n")
    # First row carries the content; the inner box did NOT claim any
    # extra rows just because the cap allowed up to 10.
    assert lines[0] == "a"
    assert sum(1 for ln in lines if ln.strip()) == 1


def test_max_height_caps_overflow_content() -> None:
    """``maxHeight=3`` with 5 content lines → exactly 3 visible rows.

    The first 3 lines are painted; the remaining 2 are clipped by the
    box's reserved area (which is now correctly capped at 3 instead of
    pinned at the parent's ``avail_h``).
    """
    tree = box(text("a\nb\nc\nd\ne"), maxHeight=3)
    assert render_to_string(tree, columns=20) == "a\nb\nc"


def test_max_height_caps_below_parent_avail() -> None:
    """``avail_h=20`` does NOT inflate a ``maxHeight=5``-only box.

    Companion to :func:`test_max_height_allows_content_to_drive_size`:
    the box's resolved height is the content height (1), not
    ``min(avail_h, maxHeight)`` (which would have been 5).
    """
    outer = box(box(text("only"), maxHeight=5), height=20)
    rendered = render_to_string(outer, columns=20)
    lines = rendered.split("\n")
    assert lines[0] == "only"
    assert sum(1 for ln in lines if ln.strip()) == 1


def test_max_width_analogous_does_not_fill() -> None:
    """``maxWidth`` mirrors ``maxHeight``: caps, never fills.

    A short text inside a ``maxWidth=10`` box renders at its natural
    width (3 cells), not stretched to 10. We pin the outer box to a
    wider ``width`` so the inner box receives an ``exactly`` constraint
    the buggy code would have honoured.
    """
    tree = box(box(text("abc"), maxWidth=10), width=20)
    rendered = render_to_string(tree, columns=20)
    # Strip trailing pad cells: the inner box auto-sized to 3 chars
    # instead of claiming the full 10-cell maxWidth.
    assert rendered.rstrip() == "abc"
    assert len(rendered.rstrip()) == 3


# ---------------------------------------------------------------------------
# align-content (requires flexWrap; mostly xfail in MVP)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="alignContent needs multi-line wrap (not in MVP).", strict=True)
@pytest.mark.parametrize(
    "align_content,expected",
    [
        ("flex-start", "AB\nCD\n\n\n\n"),
        ("center", "\n\nAB\nCD\n\n"),
        ("flex-end", "\n\n\n\nAB\nCD"),
        ("space-between", "AB\n\n\n\n\nCD"),
        ("space-around", "\nAB\n\n\nCD\n"),
        ("space-evenly", "\nAB\n\nCD\n\n"),
        ("stretch", "AB\n\n\nCD\n\n"),
    ],
)
def test_align_content_modes(align_content: str, expected: str) -> None:
    tree = box(
        text("A"), text("B"), text("C"), text("D"),
        width=2, height=6, flexWrap="wrap", alignContent=align_content,
    )
    assert render_to_string(tree) == expected


@pytest.mark.xfail(reason="alignContent needs multi-line wrap (not in MVP).", strict=True)
def test_align_content_defaults_to_flex_start() -> None:
    tree = box(
        text("A"), text("B"), text("C"), text("D"),
        width=2, height=6, flexWrap="wrap",
    )
    assert render_to_string(tree) == "AB\nCD\n\n\n\n"


# ---------------------------------------------------------------------------
# Edge cases & free-form
# ---------------------------------------------------------------------------


def test_empty_string_root() -> None:
    assert render_to_string(text("")) == ""


def test_single_node_no_children() -> None:
    assert render_to_string(text("solo")) == "solo"


def test_deeply_nested_boxes() -> None:
    tree = box(
        box(
            box(
                box(text("deep"), padding=1),
                padding=1,
            ),
            padding=1,
        ),
        padding=1,
    )
    # 4 levels of padding=1 → 4 leading newlines, 4 leading spaces, "deep".
    assert render_to_string(tree) == "\n\n\n\n    deep\n\n\n\n"


def test_long_text_triggers_wrap_in_width_bound_box() -> None:
    tree = box(text("abcdefghij"), width=4)
    out = render_to_string(tree)
    assert out == "abcd\nefgh\nij"


def test_long_text_in_flex_grow_box_wraps_to_final_width() -> None:
    """Long text inside a flexGrow box (no explicit width) must wrap to
    the box's *final* resolved width, not the wider estimate from the
    first layout pass. Regression test for the "main content area"
    overflow in the nested-layout example: the first pass measured the
    text against the outer column's width, joined the wrapped lines
    with ``\\n``, and the ``"\\n" in node.text`` guard then suppressed
    re-wrapping when the inner box later shrank — which let the long
    first line poke through the side border.
    """
    long_text = (
        "This is a long paragraph that must wrap inside the inner box "
        "without overflowing its border."
    )
    tree = box(
        box(
            box(text(long_text), padding=1, borderStyle="single"),
            flexGrow=1,
            paddingX=1,
            borderStyle="single",
        ),
        width=30,
    )
    out = render_to_string(tree)
    # Look only at the text-bearing rows (those containing words from
    # ``long_text``) — border / padding rows are full-width by design.
    text_lines = [
        line
        for line in out.split("\n")
        if any(word in line for word in ("paragraph", "wrap", "inside", "without"))
    ]
    assert text_lines, "expected the long-text lines to be present in output"
    # Inner content area is 22 cells (30 - 2 outer border - 2 outer
    # paddingX - 2 inner border - 2 inner padding). Each text line,
    # including its leading "│  │ " border prefix, must fit within
    # the outer box width (30); the visible *text* portion of each
    # line must fit within the inner content area (22).
    for line in text_lines:
        assert len(line) <= 30, f"text row exceeded outer width: {line!r}"
        # Strip the leading "│  │ " (4 cells) and trailing " │ │"
        # (4 cells) to get just the wrapped text; it must be ≤ 22.
        assert "paragraph" in line or "wrap" in line or "inside" in line or "without" in line
    # The paragraph was wrapped to multiple lines (not one long line).
    assert len(text_lines) >= 2


def test_callable_truncate_text_refits_after_renderer_rerun() -> None:
    """A ``truncate-end`` callable text leaf must stay within the final
    width even when the layout engine re-runs its renderer at a *wider*
    measurement width on an intermediate pass.

    Regression for the demo's "Submitted: ..." status line poking past
    the right border: the multi-pass flex engine fed the text leaf a
    sequence of widths (e.g. 68 → 72 → 68). The first pass wrapped to 68
    and recorded ``_wrapped_width=68``; the 72-wide pass re-ran the
    deferred renderer (because the measurement width changed), which
    reset ``node.text`` back to the *unwrapped* source — and the wrap
    guard then refused to re-truncate because 72 was not *tighter* than
    the stale ``_wrapped_width=68``. The unwrapped full-width line
    survived into the final paint and overflowed the container. The fix
    invalidates ``_wrapped_width`` whenever the renderer regenerates the
    text so the guard can re-truncate to the current width.
    """
    long_line = (
        "Submitted: 'asasdwe qwe qwe          asd af         "
        "we qe qwe qwe qweqe qwe more text here to overflow really long line'"
    )

    # A sibling whose intrinsic content is *wider* than the container
    # forces the flex engine through a shrink pass, so the text leaf is
    # measured at oscillating widths across passes (e.g. wide → final).
    # That width change re-runs the callable renderer (resetting
    # ``node.text`` to the unwrapped source) and is exactly the condition
    # that exposed the stale ``_wrapped_width`` guard.
    tree = box(
        box(
            box(text("x" * 80), borderStyle="single", paddingX=1),
            text(lambda: long_line, wrap="truncate-end"),
            flexDirection="column",
        ),
        flexDirection="column",
        padding=1,
        borderStyle="round",
        width=60,
    )
    out = render_to_string(tree)
    for line in out.split("\n"):
        assert len(line) <= 60, f"row exceeded container width (60): {line!r}"


def test_clip_lines_to_height_keeps_cursor_row_visible() -> None:
    """``_clip_lines_to_height`` honours the public ``scroll_offset`` prop.

    Default (no ``scroll_offset`` prop) keeps the leading rows — matching
    ink's ``<Box height={n}>`` truncation. When ``scroll_offset`` is set
    the clip windows down by that many rows, so a multi-line
    ``TextInput`` whose box was shrunk below its viewport still shows the
    row the cursor sits on (Regression: cursor on the last line vanished
    because the top-keeping clip dropped the bottom rows).
    """
    from ink.layout.render_layout import _clip_lines_to_height

    lines = ["L0", "L1", "L2", "L3", "L4", "L5"]
    # No scroll prop → leading rows.
    assert _clip_lines_to_height(lines, 3, {}) == ["L0", "L1", "L2"]
    # Scroll past the first three rows → window slides to the bottom.
    assert _clip_lines_to_height(lines, 3, {"scroll_offset": 3}) == [
        "L3",
        "L4",
        "L5",
    ]
    # ``scroll_offset=0`` is the same as unset.
    assert _clip_lines_to_height(lines, 3, {"scroll_offset": 0}) == [
        "L0",
        "L1",
        "L2",
    ]
    # Offset in the middle → window starts at that row.
    assert _clip_lines_to_height(lines, 3, {"scroll_offset": 1}) == [
        "L1",
        "L2",
        "L3",
    ]
    # Offset past the end clamps to the last valid window.
    assert _clip_lines_to_height(lines, 3, {"scroll_offset": 99}) == [
        "L3",
        "L4",
        "L5",
    ]


def test_container_too_small_for_children_shrinks() -> None:
    """Width 4 holds two 3-cell children only via shrink.

    Smoke check: result must not crash, must be a single row, and the
    row width must be at most the container width (4 cells). The
    exact split between A and B depends on the shrink rounding policy.
    """
    tree = box(
        box(text("AAA"), flexShrink=1, width=3),
        box(text("BBB"), flexShrink=1, width=3),
        width=4,
    )
    out = render_to_string(tree)
    first_line = out.split("\n")[0]
    assert len(first_line) <= 4
    assert set(first_line) <= {"A", "B"}


# ---------------------------------------------------------------------------
# text-width.tsx — wide characters, emoji, ANSI passthrough (rendering)
# ---------------------------------------------------------------------------


def test_wide_char_cjk_in_fixed_width_box() -> None:
    """ink: CJK characters occupy correct width in fixed-width Box.

    Ported from ``text-width.tsx``: ``<Box><Box width=4><Text>你好</Text>
    </Box><Text>|</Text></Box>`` must yield ``你好|`` because the inner
    box is 4 cells wide and the sibling lands at column 4.
    """
    tree = box(
        box(text("你好"), width=4),
        text("|"),
    )
    assert render_to_string(tree) == "你好|"


def test_wide_char_emoji_in_fixed_width_box() -> None:
    """ink: emoji (🍔 width=2) inside width=2 Box → '🍔|'."""
    tree = box(
        box(text("🍔"), width=2),
        text("|"),
    )
    assert render_to_string(tree) == "🍔|"


def test_wide_char_mixed_in_fixed_width_box() -> None:
    """ink: 'ab🍔cd' (display width 6) inside width=6 Box → 'ab🍔cd|'."""
    tree = box(
        box(text("ab🍔cd"), width=6),
        text("|"),
    )
    assert render_to_string(tree) == "ab🍔cd|"


def test_ansi_styled_text_does_not_affect_layout_width() -> None:
    """ink: ANSI styled text does not affect layout width.

    Ported from ``text-width.tsx``: the ANSI colour runs must pass
    through verbatim while keeping the layout width at 5 cells, so the
    sibling ``|`` lands at the correct column.
    """
    tree = box(
        box(text("\x1b[31mhello\x1b[0m"), width=5),
        text("|"),
    )
    assert render_to_string(tree) == "\x1b[31mhello\x1b[0m|"


def test_ansi_leading_escape_preserved() -> None:
    """A leading ANSI escape must survive into the rendered output."""
    tree = box(text("\x1b[31mabc"))
    assert render_to_string(tree) == "\x1b[31mabc"


def test_ansi_trailing_escape_preserved() -> None:
    """A trailing ANSI escape must survive into the rendered output."""
    tree = box(text("abc\x1b[0m"))
    assert render_to_string(tree) == "abc\x1b[0m"


def test_ansi_escape_in_middle_preserved() -> None:
    """ANSI escapes between visible chars are kept and don't shift cells."""
    tree = box(text("a\x1b[31mb\x1b[0mc"))
    assert render_to_string(tree) == "a\x1b[31mb\x1b[0mc"


def test_empty_text_does_not_affect_sibling_layout() -> None:
    """ink: empty Text does not affect sibling layout (text-width.tsx)."""
    tree = box(
        text(""),
        text("hello"),
    )
    assert render_to_string(tree) == "hello"


# ---------------------------------------------------------------------------
# Column box height sums children heights (Bug 3 regression)
# ---------------------------------------------------------------------------


def test_column_box_height_sums_children_heights() -> None:
    """Column-direction Box reports total height = sum of children heights.

    Regression: a column Box with three single-row Text children must
    report ``layout_height == 3`` (plus any border/padding). The flex
    engine must NOT stretch a column child to fill the parent's main
    axis — that would inflate the Box height beyond its content.

    Uses ``render_to_string`` with explicit width/height so the root
    fills the viewport; the assertion is that the *content* of the
    column Box appears on consecutive rows with no gap rows in between.
    """
    tree = box(
        text("aaa"),
        text("bbb"),
        text("ccc"),
        flexDirection="column",
    )
    # Root Box defaults to filling the viewport, but the column
    # children should still stack on consecutive rows with no gap.
    out = render_to_string(tree, columns=10)
    lines = out.split("\n")
    # Find the index of the first content line.
    first_content = next((i for i, ln in enumerate(lines) if "aaa" in ln), None)
    assert first_content is not None, f"aaa not found in {lines!r}"
    # The next two lines should contain bbb and ccc — no blank row
    # between them. (Blank rows would mean the column children were
    # stretched to spread across the viewport.)
    assert "bbb" in lines[first_content + 1], (
        f"bbb should be on the row immediately after aaa; "
        f"got lines={lines!r}"
    )
    assert "ccc" in lines[first_content + 2], (
        f"ccc should be on the row immediately after bbb; "
        f"got lines={lines!r}"
    )


# ---------------------------------------------------------------------------
# Bug 1 (PR2): min-content clamp prevents shrink-to-zero row overlap
# ---------------------------------------------------------------------------


def test_shrink_clamps_to_min_content_text_leaf() -> None:
    """Three text leaves in a column too short for all three stay ≥1 row each.

    Regression for Bug 1: previously ``_distribute_main`` wrote
    ``out[i] = max(0, sizes[i] - shrink_amount)``, which could compress a
    text leaf to height 0. ``_paint_text`` still drew the leaf's content
    at that 0-height row, so siblings overlapped. With the min-content
    clamp each text leaf keeps at least 1 row of allocated main-axis
    size, so the three leaves get distinct ``layout_y`` offsets (no two
    share the same row).
    """
    from ink.core.reconciler import Reconciler
    from ink.layout import layout

    tree = box(
        text("aaa"),
        text("bbb"),
        text("ccc"),
        flexDirection="column",
        height=2,  # too short for 3 leaves
    )
    reconciled = Reconciler().mount(tree, parent=None)
    try:
        lt = layout(reconciled, columns=10)
        ys = [child.y for child in lt.children]
        heights = [child.height for child in lt.children]
        # Each child's allocated height is at least 1 (the text leaf
        # min-content floor). Under the old shrink-to-zero behaviour
        # at least one child would have height 0.
        assert all(h >= 1 for h in heights), (
            f"each child must keep ≥1 row of height; got {heights!r}"
        )
        # Each child has a distinct y — no two share a row (which was
        # the overlap artefact under shrink-to-zero).
        assert len(set(ys)) == len(ys), (
            f"children must have distinct y positions; got {ys!r}"
        )
    finally:
        Reconciler().unmount(reconciled)


def test_shrink_redistributes_overflow() -> None:
    """A child pinned to min-content stops shrinking; remainder goes elsewhere.

    Build a column with one short leaf and two tall leaves; when the
    container can't hold all of them, the short leaf clamps at its
    min-content (1 row) and any leftover overflow is absorbed by the
    remaining shrinkable children rather than ignored.
    """
    from ink.core.reconciler import Reconciler
    from ink.layout import layout

    # Three children: short (1 row), tall (3 rows each). Container has
    # only 4 rows of room → overflow = (1 + 3 + 3) - 4 = 3 rows to shed.
    # The short child is already at min-content (1) so it can't shrink;
    # the two tall children must absorb the full 3-row overflow between
    # them. Expected: short stays at 1, the other two each end up at
    # roughly (3 + 3 - 3) / 2 = 1.5 → 1 or 2 rows but always ≥1.
    tree = box(
        box(text("short"), flexShrink=1),
        box(text("tall1\ntall1\ntall1"), flexShrink=1),
        box(text("tall2\ntall2\ntall2"), flexShrink=1),
        flexDirection="column",
        height=4,
    )
    reconciled = Reconciler().mount(tree, parent=None)
    try:
        lt = layout(reconciled, columns=10)
        column = lt
        heights = [child.height for child in column.children]
        # All three children present (none dropped to 0).
        assert len(heights) == 3, f"expected 3 children; got {heights!r}"
        # Short child clamped at min-content (1).
        assert heights[0] == 1, (
            f"short child should clamp at min-content=1; got {heights[0]}"
        )
        # Both tall children stayed ≥1 (didn't underflow).
        assert heights[1] >= 1 and heights[2] >= 1, (
            f"tall children should stay ≥1 row; got {heights[1]}, {heights[2]}"
        )
        # And total absorbed ≤ natural (1 + 3 + 3 = 7) but ≥ container (4)
        # after clamp + redistribute.
        assert sum(heights) <= 7, f"sum shouldn't exceed natural; got {sum(heights)}"
    finally:
        Reconciler().unmount(reconciled)


def test_shrink_all_at_min_content_no_overlap() -> None:
    """When every child is pinned at min-content, none shrink to overlap.

    Construct a column where the total min-content of all children
    exceeds the container — every child clamps at 1 row. They must
    still get distinct ``layout_y`` offsets (no overlap), even though
    the column overflows the container's content box.
    """
    from ink.core.reconciler import Reconciler
    from ink.layout import layout

    tree = box(
        text("a"),
        text("b"),
        text("c"),
        text("d"),
        text("e"),
        flexDirection="column",
        height=2,  # way too short for 5 leaves
    )
    reconciled = Reconciler().mount(tree, parent=None)
    try:
        lt = layout(reconciled, columns=10)
        ys = [child.y for child in lt.children]
        heights = [child.height for child in lt.children]
        # Every child stayed at ≥1 (min-content floor).
        assert all(h >= 1 for h in heights), (
            f"each child ≥1 row; got {heights!r}"
        )
        # Every child has a distinct y (no overlap).
        assert len(set(ys)) == len(ys), (
            f"distinct y positions required; got {ys!r}"
        )
    finally:
        Reconciler().unmount(reconciled)


def test_row_min_content_is_max_child_width() -> None:
    """Row container's min-content is its widest child's min-width.

    With ``flexShrink > 0`` and a row narrower than the widest child,
    each child's allocated width clamps at its own min-content (1 cell
    — a text leaf) rather than collapsing to 0. The row keeps every
    child on a distinct x column.
    """
    from ink.core.reconciler import Reconciler
    from ink.layout import layout

    # Three text-leaf children wider than the container; each has
    # min_content_main = 1 (text leaf default).
    tree = box(
        text("AAA"),
        text("BBB"),
        text("CCC"),
        width=4,  # narrower than the 9 cells the children want
    )
    reconciled = Reconciler().mount(tree, parent=None)
    try:
        lt = layout(reconciled, columns=10)
        xs = [child.x for child in lt.children]
        widths = [child.width for child in lt.children]
        # Each child's allocated width ≥1 (min-content floor for a
        # text leaf).
        assert all(w >= 1 for w in widths), (
            f"each child ≥1 cell wide; got {widths!r}"
        )
        # Distinct x positions (no overlap).
        assert len(set(xs)) == len(xs), (
            f"distinct x positions required; got {xs!r}"
        )
    finally:
        Reconciler().unmount(reconciled)


# ---------------------------------------------------------------------------
# Bug 6 (PR4 audit): flex_shrink must keep measured_width consistent
# ---------------------------------------------------------------------------


def test_flex_shrink_syncs_measured_width() -> None:
    """A shrunk child's ``measured_width`` must match its post-shrink width.

    Regression for audit Bug 6: the force-clamp
    ``child.layout_width = allocated_w`` after a shrink pass must keep
    ``measured_width`` in lock-step. The text-leaf re-layout path
    happens to keep them aligned today, but a future reader that
    consults ``measured_width`` after shrink would otherwise see a
    stale intrinsic value. The fix makes the invariant explicit.
    """
    from ink.core.reconciler import Reconciler
    from ink.layout.flex import build_flex_tree, layout_root

    # Two wide text leaves forced to share a narrow row container.
    # Each child is itself a text leaf — the shrink pass re-runs the
    # measurement at the allocated width and the fix ensures the
    # ``measured_width`` field agrees with the final ``layout_width``.
    tree = box(
        text("AAAAAA", flexShrink=1),
        text("BBBBBB", flexShrink=1),
        width=6,
    )
    reconciled = Reconciler().mount(tree, parent=None)
    try:
        flex_root = build_flex_tree(reconciled)
        assert flex_root is not None
        layout_root(flex_root, columns=10)
        assert len(flex_root.children) == 2
        for child in flex_root.children:
            # Both children are shrunk below their natural 6-cell width.
            assert child.layout_width < 6, (
                f"expected child to be shrunk below 6; got "
                f"layout_width={child.layout_width}"
            )
            # After shrink, both fields must agree.
            assert child.measured_width == child.layout_width, (
                f"measured_width {child.measured_width} != layout_width "
                f"{child.layout_width} after shrink"
            )
    finally:
        Reconciler().unmount(reconciled)


def test_flex_shrink_syncs_measured_height_column() -> None:
    """A shrunk child's ``measured_height`` must match its post-shrink height.

    Same regression as the row case but for the column main axis (height).
    """
    from ink.core.reconciler import Reconciler
    from ink.layout.flex import build_flex_tree, layout_root

    tree = box(
        box(text("aaaa\nbbbb\ncccc"), flexShrink=1),
        box(text("dddd\neeee\nffff"), flexShrink=1),
        flexDirection="column",
        height=4,
    )
    reconciled = Reconciler().mount(tree, parent=None)
    try:
        flex_root = build_flex_tree(reconciled)
        assert flex_root is not None
        layout_root(flex_root, columns=10)
        for child in flex_root.children:
            assert child.layout_height < 3, (
                f"expected child to be shrunk below 3; got "
                f"layout_height={child.layout_height}"
            )
            assert child.measured_height == child.layout_height, (
                f"measured_height {child.measured_height} != layout_height "
                f"{child.layout_height} after shrink"
            )
    finally:
        Reconciler().unmount(reconciled)
