"""Tests for :func:`pyink.components.Box` (PR4).

Covers:

* Element creation (children + props flow through to ``element.props``).
* Layout-style props are passed verbatim (delegated to FlexStyle —
  already covered by ``tests/layout/test_flex.py``).
* Border rendering: each ``borderStyle`` alias, custom dicts, single-
  edge visibility flags, per-edge colour / dim / background.
* Box background fill across the content area (with and without
  borders, with padding).

Many of the expected strings are ported from ink's
``test/borders.tsx`` / ``test/border-backgrounds.tsx``. Where ink uses
``alignSelf="flex-start"`` on the *root* box to opt out of full-width
layout, PyInk wraps the box in an outer ``Box`` to achieve the same
fit-content behaviour (PyInk's root always fills the configured
columns).
"""

from __future__ import annotations

from pyink import Box, Spacer, Text, render_to_string
from pyink.components.box import Box as BoxDirect
from pyink.core.element import Element

ESC = "\x1b"

# ---------------------------------------------------------------------------
# Element construction
# ---------------------------------------------------------------------------


def test_box_creates_host_element() -> None:
    el = Box(Text("hi"))
    assert isinstance(el, Element)
    assert el.type == "box"
    assert len(el.children) == 1


def test_box_direct_import_matches_public() -> None:
    assert BoxDirect is Box


def test_box_passes_props_verbatim() -> None:
    el = Box(Text("x"), flexDirection="column", padding=2, borderStyle="round")
    assert el.props["flexDirection"] == "column"
    assert el.props["padding"] == 2
    assert el.props["borderStyle"] == "round"


def test_box_flattens_tuple_children() -> None:
    inner = (Text("a"), Text("b"))
    el = Box(*inner, Text("c"))
    assert len(el.children) == 3


def test_box_filters_none_children() -> None:
    el = Box(Text("a"), None, Text("b"))
    assert len(el.children) == 2


def test_box_accepts_list_comprehension_unpacking() -> None:
    el = Box(*[Text(c) for c in "abc"])
    assert len(el.children) == 3


def test_box_no_children() -> None:
    el = Box()
    assert el.type == "box"
    assert el.children == ()


# ---------------------------------------------------------------------------
# Border styles — ported from ink's borders.tsx (fit-content variant)
# ---------------------------------------------------------------------------


def test_border_round_fit_content() -> None:
    """ink: ``<Box borderStyle="round" alignSelf="flex-start">``."""
    tree = Box(
        Box(Text("Hello World"), borderStyle="round"),
        alignSelf="flex-start",
    )
    assert render_to_string(tree) == "╭───────────╮\n│Hello World│\n╰───────────╯"


def test_border_single_fit_content() -> None:
    tree = Box(Box(Text("Hi"), borderStyle="single"), alignSelf="flex-start")
    assert render_to_string(tree) == "┌──┐\n│Hi│\n└──┘"


def test_border_double_fit_content() -> None:
    tree = Box(Box(Text("Hi"), borderStyle="double"), alignSelf="flex-start")
    assert render_to_string(tree) == "╔══╗\n║Hi║\n╚══╝"


def test_border_bold_fit_content() -> None:
    tree = Box(Box(Text("Hi"), borderStyle="bold"), alignSelf="flex-start")
    assert render_to_string(tree) == "┏━━┓\n┃Hi┃\n┗━━┛"


def test_border_round_full_width_default() -> None:
    """A border box without alignSelf fills the parent's width."""
    tree = Box(Text("Hello World"), borderStyle="round", width=15)
    out = render_to_string(tree)
    assert out == "╭─────────────╮\n│Hello World  │\n╰─────────────╯"


def test_border_with_padding() -> None:
    tree = Box(Text("Hello World"), borderStyle="round", padding=1, width=15)
    out = render_to_string(tree)
    lines = out.split("\n")
    assert lines[0] == "╭─────────────╮"
    assert lines[1] == "│             │"
    # padding=1 → 1 space of inner padding on each side of "Hello World"
    # (11) → " Hello World " within the 13-cell content area.
    assert lines[2] == "│ Hello World │"
    assert lines[3] == "│             │"
    assert lines[4] == "╰─────────────╯"


def test_border_round_wide_characters() -> None:
    """ink: fit-content round box around CJK text."""
    tree = Box(Box(Text("こんにちは"), borderStyle="round"), alignSelf="flex-start")
    # 5 CJK chars = width 10 → 10 dashes top/bottom.
    assert render_to_string(tree) == "╭──────────╮\n│こんにちは│\n╰──────────╯"


def test_border_round_emoji() -> None:
    tree = Box(Box(Text("🌊🌊"), borderStyle="round"), alignSelf="flex-start")
    out = render_to_string(tree)
    # Each emoji is 2 cells → 4 dashes top/bottom.
    assert out == "╭────╮\n│🌊🌊│\n╰────╯"


# ---------------------------------------------------------------------------
# Border single-edge visibility — ported from ink borders.tsx
# ---------------------------------------------------------------------------


def test_border_hide_top() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderTop=False),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            "│Content│",
            "╰───────╯",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_hide_bottom() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderBottom=False),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            "╭───────╮",
            "│Content│",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_hide_top_and_bottom() -> None:
    tree = Box(
        Text("Above"),
        Box(
            Text("Content"),
            borderStyle="round",
            borderTop=False,
            borderBottom=False,
        ),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(["Above", "│Content│", "Below"])
    assert render_to_string(tree) == expected


def test_border_hide_left() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderLeft=False),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            "───────╮",
            "Content│",
            "───────╯",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_hide_right() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderRight=False),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            "╭───────",
            "│Content",
            "╰───────",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_hide_all() -> None:
    tree = Box(
        Text("Above"),
        Box(
            Text("Content"),
            borderStyle="round",
            borderTop=False,
            borderBottom=False,
            borderLeft=False,
            borderRight=False,
        ),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    assert render_to_string(tree) == "Above\nContent\nBelow"


# ---------------------------------------------------------------------------
# Per-edge border colour / dim — ported from ink borders.tsx
# ---------------------------------------------------------------------------


def test_border_top_color() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderTopColor="green"),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            f"{ESC}[32m╭───────╮{ESC}[0m",
            "│Content│",
            "╰───────╯",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_bottom_color() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderBottomColor="green"),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            "╭───────╮",
            "│Content│",
            f"{ESC}[32m╰───────╯{ESC}[0m",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_left_color() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderLeftColor="green"),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            "╭───────╮",
            f"{ESC}[32m│{ESC}[0mContent│",
            "╰───────╯",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_right_color() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderRightColor="green"),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            "╭───────╮",
            f"│Content{ESC}[32m│{ESC}[0m",
            "╰───────╯",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_dim_color_all() -> None:
    """``borderDimColor`` dims every edge."""
    tree = Box(
        Box(Text("Content"), borderStyle="round", borderDimColor=True),
        alignSelf="flex-start",
    )
    expected = "\n".join(
        [
            f"{ESC}[2m╭───────╮{ESC}[0m",
            f"{ESC}[2m│{ESC}[0mContent{ESC}[2m│{ESC}[0m",
            f"{ESC}[2m╰───────╯{ESC}[0m",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_top_dim_color() -> None:
    tree = Box(
        Text("Above"),
        Box(Text("Content"), borderStyle="round", borderTopDimColor=True),
        Text("Below"),
        flexDirection="column",
        alignItems="flex-start",
    )
    expected = "\n".join(
        [
            "Above",
            f"{ESC}[2m╭───────╮{ESC}[0m",
            "│Content│",
            "╰───────╯",
            "Below",
        ]
    )
    assert render_to_string(tree) == expected


def test_border_color_all_edges() -> None:
    tree = Box(
        Box(Text("Content"), borderStyle="round", borderColor="green"),
        alignSelf="flex-start",
    )
    expected = "\n".join(
        [
            f"{ESC}[32m╭───────╮{ESC}[0m",
            f"{ESC}[32m│{ESC}[0mContent{ESC}[32m│{ESC}[0m",
            f"{ESC}[32m╰───────╯{ESC}[0m",
        ]
    )
    assert render_to_string(tree) == expected


# ---------------------------------------------------------------------------
# Custom borderStyle as dict — ported from ink borders.tsx "custom border style"
# ---------------------------------------------------------------------------


def test_custom_border_style_dict() -> None:
    custom = {
        "topLeft": "↘",
        "top": "↓",
        "topRight": "↙",
        "right": "←",
        "bottomRight": "↖",
        "bottom": "↑",
        "bottomLeft": "↗",
        "left": "→",
    }
    tree = Box(Box(Text("Content"), borderStyle=custom), alignSelf="flex-start")
    out = render_to_string(tree)
    assert out == "\n".join(
        [
            "↘↓↓↓↓↓↓↓↙",
            "→Content←",
            "↗↑↑↑↑↑↑↑↖",
        ]
    )


# ---------------------------------------------------------------------------
# Background fill — ported from ink background.tsx
# ---------------------------------------------------------------------------


def test_background_color_text_inherits() -> None:
    """Box backgroundColor paints the entire area; text renders on top."""
    tree = Box(
        Box(Text("Hello World"), backgroundColor="green"),
        alignItems="flex-start",
    )
    out = render_to_string(tree)
    # The full 11-cell row carries the green background.
    assert out == f"{ESC}[42mHello World{ESC}[0m"


def test_background_color_with_border() -> None:
    """Background fills the box's interior (inside the border)."""
    tree = Box(
        Text("Hi"),
        backgroundColor="cyan",
        borderStyle="round",
        width=6,
        height=5,
        alignSelf="flex-start",
    )
    out = render_to_string(tree)
    # Border is drawn, then background fill of interior cells (4 wide × 3 tall).
    lines = out.split("\n")
    assert lines[0] == "╭────╮"
    assert "Hi" in lines[1] or "Hi" in lines[2]
    # Background cyan should appear at least once in the output.
    assert f"{ESC}[46m" in out
    assert f"{ESC}[0m" in out


def test_background_color_hex() -> None:
    tree = Box(Text("Hi"), backgroundColor="#FF0000", width=4, alignSelf="flex-start")
    out = render_to_string(tree)
    assert f"{ESC}[48;2;255;0;0m" in out
    assert "Hi" in out


def test_background_color_rgb() -> None:
    tree = Box(Text("Hi"), backgroundColor="rgb(255, 0, 0)", width=4, alignSelf="flex-start")
    out = render_to_string(tree)
    assert f"{ESC}[48;2;255;0;0m" in out


def test_background_color_ansi256() -> None:
    tree = Box(Text("Hi"), backgroundColor="ansi256(9)", width=4, alignSelf="flex-start")
    out = render_to_string(tree)
    assert f"{ESC}[48;5;9m" in out


# ---------------------------------------------------------------------------
# Nested boxes with borders — ported from ink borders.tsx nested cases
# ---------------------------------------------------------------------------


def test_nested_boxes_row_direction() -> None:
    tree = Box(
        Box(Text("A"), borderStyle="round"),
        Box(Text("B"), borderStyle="round"),
        alignSelf="flex-start",
    )
    out = render_to_string(tree)
    # Two 3-wide boxes side by side.
    assert out == "╭─╮╭─╮\n│A││B│\n╰─╯╰─╯"


def test_nested_boxes_column_direction() -> None:
    tree = Box(
        Box(Text("A"), borderStyle="round"),
        Box(Text("B"), borderStyle="round"),
        flexDirection="column",
        alignItems="flex-start",
    )
    out = render_to_string(tree)
    assert out == "╭─╮\n│A│\n╰─╯\n╭─╮\n│B│\n╰─╯"


# ---------------------------------------------------------------------------
# Edge cases — PR4 check list spot checks
# ---------------------------------------------------------------------------


def test_three_level_nested_borders() -> None:
    """Box→Box→Box with borders compose without cell corruption."""
    tree = Box(
        Box(
            Box(Text("deep"), borderStyle="single"),
            borderStyle="round",
            padding=1,
        ),
        alignSelf="flex-start",
    )
    out = render_to_string(tree)
    expected = "\n".join(
        [
            "╭────────╮",
            "│        │",
            "│ ┌────┐ │",
            "│ │deep│ │",
            "│ └────┘ │",
            "│        │",
            "╰────────╯",
        ]
    )
    assert out == expected


def test_border_text_overflow_triggers_wrap() -> None:
    """Long text inside a fixed-width bordered box wraps inside the border.

    Border occupies 1 cell per visible side, so a width=10 box has an
    8-cell content area. Word-wrap fits ``Hello World`` as
    ``Hello\nWorld`` (each ≤ 8 cells).
    """
    tree = Box(Text("Hello World"), borderStyle="round", width=10)
    out = render_to_string(tree)
    expected = "\n".join(
        [
            "╭────────╮",
            "│Hello   │",
            "│World   │",
            "╰────────╯",
        ]
    )
    assert out == expected


def test_spacer_in_column_with_borders() -> None:
    """Spacer fills vertical space inside a bordered column.

    The outer Box has ``height=5`` and a 1-cell border on every side,
    leaving a 3-row interior. With ``A`` + ``B`` taking 2 rows, the
    Spacer fills the remaining 1 row.
    """
    tree = Box(
        Box(
            Text("A"),
            Spacer(),
            Text("B"),
            flexDirection="column",
            height=5,
            borderStyle="round",
        ),
        alignSelf="flex-start",
    )
    out = render_to_string(tree)
    expected = "\n".join(
        [
            "╭─╮",
            "│A│",
            "│ │",
            "│B│",
            "╰─╯",
        ]
    )
    assert out == expected
