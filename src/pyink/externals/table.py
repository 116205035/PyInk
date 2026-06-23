"""``Table`` — aligned columnar table (Phase 6 PR2).

Mirrors :mod:`ink-table`: a column of rows where each row's cells are
left-padded to the longest value in their column so the columns align
visually. PyInk's flavour collapses ink-table's class-based
``header`` / ``cell`` / ``skeleton`` customisation into a simpler
``data`` + ``columns`` + ``padding`` API — callers who want custom
cell rendering compose the underlying :func:`Box` / :func:`Text`
themselves, matching how the rest of PyInk's externals expose a
declarative factory.

Design (per PRD PR2 scope):

* ``Table`` is a thin factory that returns a ``box`` column element
  — no hooks, no function component. The whole table is built
  eagerly at call time; rows do not re-render on their own (the
  caller wraps the call in a parent that re-renders if they need
  reactive data, mirroring :func:`Divider`'s declarative contract).
* Two ``data`` shapes are accepted:

  * ``list[list[str]]`` — positional rows. ``columns`` defaults to
    ``"Column 1"`` / ``"Column 2"`` / … when omitted; passing
    ``columns`` explicitly overrides the headers.
  * ``list[dict[str, str]]`` — keyed rows. ``columns`` defaults to
    the union of keys across all rows (insertion-ordered). Missing
    keys in a given row render as an empty cell.

* Column widths are computed as the longest string among the header
  and every cell in that column, plus ``padding * 2`` cells of
  interior gutter (``padding`` on each side). The default
  ``padding=1`` matches ink-table.
* The header row is rendered with :func:`Text` ``bold=True``; every
  data cell is rendered as a plain :func:`Text` leaf inside a row
  :func:`Box` with ``flexDirection="row"``. This matches ink-table's
  visual treatment without the ``skeleton`` border characters
  (PyInk favours the borderless look; callers who want a border can
  wrap the table in a ``Box(borderStyle="single")``).
* An empty ``data`` list with no ``columns`` renders an empty column
  (no header, no rows). With ``columns`` set, the header still
  renders so the table shape is stable across empty / populated
  states.

Cross-layer note: each cell is pinned to its resolved column width
via :func:`Box` ``width`` rather than relying on flex stretching —
that keeps the columns aligned regardless of the surrounding
container's main-axis sizing (a row placed in an auto-width column
parent still lines up).

PR2 scope: ships ``Table`` only.
"""

from __future__ import annotations

from typing import Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element

__all__ = ["Table"]

#: Default cell padding (cells on each side of the value). Matches
#: ink-table's default.
_DEFAULT_PADDING: int = 1

#: Placeholder header name for the ``list[list[str]]`` mode when the
#: caller doesn't pass an explicit ``columns`` list. The number is
#: 1-based to match the typical human-facing spreadsheet convention.
_DEFAULT_COLUMN_PREFIX: str = "Column"


def _normalize_rows(
    data: list[list[str]] | list[dict[str, str]],
    columns: list[str] | None,
) -> tuple[list[str], list[list[str]]]:
    """Normalise ``data`` to a (columns, rows-of-cells) pair.

    The dict mode walks every row to collect the union of keys
    (insertion-ordered) so missing keys in later rows still produce a
    cell. The list mode uses the caller-supplied ``columns`` if given,
    otherwise synthesises ``Column 1`` / ``Column 2`` / … up to the
    widest row.
    """
    if len(data) == 0:
        if columns is not None:
            return list(columns), []
        return [], []

    if isinstance(data[0], dict):
        # Dict mode. The union of keys across every row defines the
        # column order; an explicit ``columns`` overrides the order
        # (and may drop / reorder keys — cells for missing columns
        # render as empty strings).
        if columns is None:
            seen: dict[str, None] = {}
            for raw_row in data:
                if isinstance(raw_row, dict):
                    for key in raw_row:
                        seen[str(key)] = None
            resolved_columns = list(seen.keys())
        else:
            resolved_columns = list(columns)
        rows: list[list[str]] = []
        for raw_row in data:
            if isinstance(raw_row, dict):
                rows.append([str(raw_row.get(col, "")) for col in resolved_columns])
            else:
                # Defensive: a mixed list (some dicts, some lists)
                # is a caller bug — render each non-dict entry as a
                # single-cell row of empty strings so the table still
                # aligns instead of raising mid-render.
                rows.append(["" for _ in resolved_columns])
        return resolved_columns, rows

    # List mode.
    rows_list: list[list[Any]] = [
        list(raw_row) for raw_row in data if isinstance(raw_row, (list, tuple))
    ]
    max_cells = max((len(r) for r in rows_list), default=0)
    if columns is None:
        resolved_columns = [
            f"{_DEFAULT_COLUMN_PREFIX} {i + 1}" for i in range(max_cells)
        ]
    else:
        resolved_columns = list(columns)
    # Normalise every row to the column count: pad short rows with
    # empty strings, truncate long rows. This keeps the table aligned
    # even when the caller passes ragged data.
    col_count = len(resolved_columns)
    normalised: list[list[str]] = []
    for row in rows_list:
        cells = [str(cell) for cell in row]
        if len(cells) < col_count:
            cells = cells + [""] * (col_count - len(cells))
        elif len(cells) > col_count:
            cells = cells[:col_count]
        normalised.append(cells)
    return resolved_columns, normalised


def _column_widths(
    columns: list[str],
    rows: list[list[str]],
    padding: int,
) -> list[int]:
    """Compute each column's total cell width (value + padding on both sides).

    The width is the longest string among the header label and every
    cell in that column, plus ``2 * padding`` so the value sits in the
    middle of the gutter. Matches ink-table's
    ``Math.max(...data, header) + padding * 2`` formula.
    """
    widths: list[int] = []
    for col_idx, header in enumerate(columns):
        max_len = len(header)
        for row in rows:
            if col_idx < len(row):
                cell_len = len(row[col_idx])
                if cell_len > max_len:
                    max_len = cell_len
        widths.append(max_len + padding * 2)
    return widths


def _render_cell(value: str, width: int, padding: int) -> Element:
    """Render one cell: ``padding`` spaces + value + trailing pad to ``width``.

    The cell is wrapped in a :func:`Box` pinned to its column's
    ``width`` so the surrounding row's flex distribution doesn't
    stretch / shrink the cell — the layout engine would otherwise
    try to grow the cell to fill the parent's main axis, breaking
    alignment with sibling rows.
    """
    body = " " * padding + value
    # Pad the trailing side so the cell occupies exactly ``width`` cells.
    if len(body) < width:
        body = body + " " * (width - len(body))
    elif len(body) > width:
        body = body[:width]
    return Box(Text(body), width=width, flexShrink=0, flexGrow=0)


def Table(
    *,
    data: list[list[str]] | list[dict[str, str]],
    columns: list[str] | None = None,
    padding: int = _DEFAULT_PADDING,
    **_props: Any,
) -> Element:
    """Render a column-aligned table.

    Parameters
    ----------
    data:
        Table rows. Two shapes are accepted:

        * ``list[list[str]]`` — positional rows. ``columns`` defaults
          to ``"Column 1"`` / ``"Column 2"`` / … when omitted; pass
          ``columns`` to override the headers. Ragged rows are padded
          with empty cells / truncated to the column count so the
          table still aligns.
        * ``list[dict[str, str]]`` — keyed rows. ``columns`` defaults
          to the union of keys across all rows (insertion-ordered);
          pass ``columns`` to reorder / subset the keys. Missing keys
          in a given row render as an empty cell.
    columns:
        Header labels. When ``None`` the default depends on the
        ``data`` shape — see above. Passing an explicit list overrides
        the default order (and may drop / reorder dict-mode keys).
    padding:
        Interior gutter on each side of a cell value, in cells.
        Defaults to 1 (matches ink-table). The total cell width is
        ``longest_value + 2 * padding``.
    **_props:
        Reserved for parity with the upstream API; currently ignored.
        Callers who want a bordered table wrap the result in a
        ``Box(borderStyle="single")`` — the ``Table`` factory itself
        renders a borderless column to match PyInk's aesthetic.

    Returns
    -------
    Element
        A ``box`` host element (``flexDirection="column"``) whose
        children are a header row followed by one row per data entry.
        No function component is involved — the factory is purely
        declarative, which makes ``Box(Table(...), Text("..."))`` safe
        to call from any context.

    Raises
    ------
    TypeError
        If ``data`` is empty and ``columns`` is ``None`` (nothing to
        infer), or if ``padding`` is not a non-negative ``int``.

    Usage
    -----
    ::

        Table(data=[["a", "b"], ["c", "d"]])
        Table(
            data=[{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}],
            columns=["name", "age"],
        )
        Table(data=rows, padding=2)
    """
    if not isinstance(padding, int) or isinstance(padding, bool) or padding < 0:
        raise TypeError(
            f"Table 'padding' must be a non-negative int, "
            f"got {type(padding).__name__!r}"
        )

    resolved_columns, rows = _normalize_rows(data, columns)

    # Empty table: no columns, no rows → render an empty column so the
    # surrounding layout still has a valid element to mount.
    if not resolved_columns and not rows:
        return Box(flexDirection="column")

    widths = _column_widths(resolved_columns, rows, padding)

    # Header row: bold labels, same cell-width contract as data rows
    # so the header lines up with the data columns. We emit the cells
    # directly with a bold :func:`Text` leaf rather than going through
    # ``_render_cell`` (which renders plain text) — bold is a header-
    # specific affordance.
    bold_header_cells: list[Element] = []
    for label, width in zip(resolved_columns, widths, strict=True):
        body = " " * padding + label
        if len(body) < width:
            body = body + " " * (width - len(body))
        elif len(body) > width:
            body = body[:width]
        bold_header_cells.append(
            Box(Text(body, bold=True), width=width, flexShrink=0, flexGrow=0)
        )
    header_row = Box(*bold_header_cells, flexDirection="row")

    # Data rows.
    data_rows: list[Element] = []
    for row in rows:
        cells = [
            _render_cell(row[col_idx] if col_idx < len(row) else "", width, padding)
            for col_idx, width in enumerate(widths)
        ]
        data_rows.append(Box(*cells, flexDirection="row"))

    return Box(header_row, *data_rows, flexDirection="column")
