"""Tests for :func:`pyink.externals.Table` (Phase 6 PR2).

``Table`` is a declarative factory that returns a ``box`` column — no
hooks, no function component, no live render pipeline needed. Every
assertion uses the synchronous :func:`render_to_string` test renderer.

Coverage:

* Element shape — ``Table`` returns a ``box`` host element.
* ``list[list[str]]`` mode: default headers, custom headers, ragged
  rows, empty rows.
* ``list[dict[str, str]]`` mode: default columns (union of keys),
  custom columns (subset / reorder), missing keys render as empty.
* Column alignment: cells in the same column line up by width.
* ``padding`` controls the gutter.
* Header row is bold.
* Empty ``data=[]`` with / without ``columns``.
* ``padding`` validation.
* ``Table`` is exported from ``pyink.externals`` but NOT from the
  top-level ``pyink`` package.
"""

from __future__ import annotations

from typing import Any

from pyink import Box, render_to_string
from pyink.core.element import Element
from pyink.externals import Table

# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_table_returns_box_host_element() -> None:
    """``Table`` is a declarative factory — output is a ``box`` host."""
    el = Table(data=[["a"]])
    assert isinstance(el, Element)
    assert el.type == "box"
    assert el.props.get("flexDirection") == "column"


def test_table_empty_data_no_columns_renders_empty_column() -> None:
    """``data=[]`` + ``columns=None`` -> empty column (no header)."""
    el = Table(data=[])
    assert el.type == "box"
    # No children (no header row, no data rows).
    assert el.children == ()


# ---------------------------------------------------------------------------
# list[list[str]] mode
# ---------------------------------------------------------------------------


def test_table_list_mode_default_columns() -> None:
    """Without ``columns``, headers default to ``Column 1`` / ``Column 2``."""
    tree: Any = Box(
        Table(data=[["a", "b"], ["c", "d"]]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # Header + 2 data rows = 3 lines.
    assert len(lines) == 3
    assert "Column 1" in lines[0]
    assert "Column 2" in lines[0]
    assert "a" in lines[1]
    assert "b" in lines[1]
    assert "c" in lines[2]
    assert "d" in lines[2]


def test_table_list_mode_custom_columns() -> None:
    """Explicit ``columns`` overrides the default header labels."""
    tree: Any = Box(
        Table(
            data=[["a", "b"], ["c", "d"]],
            columns=["name", "value"],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert "name" in lines[0]
    assert "value" in lines[0]


def test_table_list_mode_ragged_rows_padded() -> None:
    """Short rows are padded with empty cells; long rows truncated."""
    tree: Any = Box(
        Table(
            data=[["a"], ["b", "c", "d"]],
            columns=["c1", "c2"],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # The table should render without raising and produce 3 lines.
    lines = out.split("\n")
    assert len(lines) == 3


def test_table_list_mode_single_row() -> None:
    """A single-row table renders header + 1 data row."""
    tree: Any = Box(
        Table(data=[["x", "y"]]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# list[dict[str, str]] mode
# ---------------------------------------------------------------------------


def test_table_dict_mode_default_columns() -> None:
    """Dict mode: ``columns`` defaults to the union of keys."""
    tree: Any = Box(
        Table(
            data=[
                {"name": "Alice", "age": "30"},
                {"name": "Bob", "age": "25"},
            ],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == 3  # header + 2 rows
    assert "name" in lines[0]
    assert "age" in lines[0]
    assert "Alice" in out
    assert "Bob" in out


def test_table_dict_mode_custom_columns_reorder() -> None:
    """``columns`` reorders the dict keys."""
    tree: Any = Box(
        Table(
            data=[{"a": "1", "b": "2"}],
            columns=["b", "a"],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # Header line: "b" appears before "a".
    header = lines[0]
    assert header.index("b") < header.index("a")


def test_table_dict_mode_custom_columns_subset() -> None:
    """``columns`` may drop keys."""
    tree: Any = Box(
        Table(
            data=[{"a": "1", "b": "2", "c": "3"}],
            columns=["a", "c"],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # 2 columns in the header.
    assert "a" in lines[0]
    assert "c" in lines[0]
    # "b" should not appear in the data (dropped column).
    assert "2" not in out


def test_table_dict_mode_missing_key_renders_empty() -> None:
    """Missing keys in a row render as an empty cell."""
    tree: Any = Box(
        Table(
            data=[
                {"a": "1", "b": "2"},
                {"a": "3"},  # missing "b"
            ],
            columns=["a", "b"],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # The table renders without raising; "3" appears in row 2.
    assert "3" in out


def test_table_dict_mode_union_of_keys() -> None:
    """Default ``columns`` collects keys from every row."""
    tree: Any = Box(
        Table(
            data=[
                {"a": "1"},
                {"b": "2"},  # different key
            ],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # Both keys should appear in the header.
    assert "a" in out
    assert "b" in out


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_table_column_alignment() -> None:
    """Cells in the same column line up by width.

    Two rows where row 1's first cell is short ("a") and row 2's first
    cell is long ("abc"). The "a" cell should be padded so the second
    column lines up across both rows.
    """
    tree: Any = Box(
        Table(
            data=[["a", "x"], ["abc", "y"]],
            columns=["c1", "c2"],
        ),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # Header + 2 data rows.
    assert len(lines) == 3
    # Both data rows should have the same length (cell-width pinned).
    assert len(lines[1]) == len(lines[2])


def test_table_padding_widens_cells() -> None:
    """``padding=2`` adds 2 cells on each side of every value."""
    tree_pad0: Any = Box(
        Table(
            data=[["a"]],
            columns=["c"],
            padding=0,
        ),
        flexDirection="column",
        width=40,
    )
    tree_pad2: Any = Box(
        Table(
            data=[["a"]],
            columns=["c"],
            padding=2,
        ),
        flexDirection="column",
        width=40,
    )
    out0 = render_to_string(tree_pad0)
    out2 = render_to_string(tree_pad2)
    # padding=2 should produce wider cells than padding=0.
    line0 = out0.split("\n")[1]
    line2 = out2.split("\n")[1]
    assert len(line2) > len(line0)


# ---------------------------------------------------------------------------
# Header styling
# ---------------------------------------------------------------------------


def test_table_header_is_bold() -> None:
    """The header row's cells are rendered with ``bold=True``."""
    tree: Any = Box(
        Table(data=[["a"]], columns=["c"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # The header line contains the SGR bold sequence (\x1b[1m).
    header_line = out.split("\n")[0]
    assert "\x1b[1m" in header_line


def test_table_data_cells_not_bold() -> None:
    """Data cells are not bold by default."""
    tree: Any = Box(
        Table(data=[["a"]], columns=["c"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # Data row should not have bold SGR.
    assert "\x1b[1m" not in lines[1]


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


def test_table_empty_data_with_columns_renders_header_only() -> None:
    """``data=[]`` with ``columns`` set renders just the header row."""
    tree: Any = Box(
        Table(data=[], columns=["a", "b"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == 1  # header only
    assert "a" in lines[0]
    assert "b" in lines[0]


def test_table_single_column() -> None:
    """Single-column tables work."""
    tree: Any = Box(
        Table(data=[["a"], ["b"]], columns=["only"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == 3
    assert "only" in lines[0]
    assert "a" in lines[1]
    assert "b" in lines[2]


def test_table_long_values_dont_break_layout() -> None:
    """Long cell values widen their column; sibling cells still align."""
    tree: Any = Box(
        Table(
            data=[["short", "x"], ["verylongvalue", "y"]],
            columns=["c1", "c2"],
        ),
        flexDirection="column",
        width=60,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # Both data rows should have the same length.
    assert len(lines[1]) == len(lines[2])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_table_negative_padding_raises() -> None:
    """``padding`` must be non-negative."""
    try:
        Table(data=[["a"]], padding=-1)
    except TypeError as exc:
        assert "padding" in str(exc)
    else:
        raise AssertionError("negative padding should raise TypeError")


def test_table_bool_padding_raises() -> None:
    """``padding`` must not be a bool."""
    try:
        Table(data=[["a"]], padding=True)
    except TypeError:
        pass
    else:
        raise AssertionError("bool padding should raise TypeError")


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_table_inside_column_with_sibling_text() -> None:
    """``Table`` composes inside a larger layout."""
    tree: Any = Box(
        "Title",
        Table(data=[["a"]], columns=["c"]),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert "Title" in out
    assert "a" in out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_table() -> None:
    from pyink.externals import Table as InitTable

    assert InitTable is Table


def test_table_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "Table")
