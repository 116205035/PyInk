"""``StructuredDiff`` — file-edit diff display (Phase 3 PR5).

Mirrors Claude Code's ``<StructuredDiff>``: turn two snapshots of a file
(``before`` / ``after``) into a column of coloured rows, one per
``difflib.unified_diff`` output line. ``+`` lines are green, ``-`` lines
are red, ``@@ ... @@`` hunk headers are magenta, context lines inherit
the terminal default. Optional counts (``+N`` / ``-M``) appear in a
header line; an optional ``language`` activates per-line Pygments
highlighting of the ``+`` / ``-`` bodies via :func:`HighlightedCode`.

``difflib`` is stdlib — no optional dependency for the diff machinery.
The only optional dependency is :mod:`pygments`, and even that is only
touched when the caller passes a non-``text`` ``language`` *and* the
package is importable; otherwise the per-line bodies fall back to plain
coloured ``Text`` leaves.

Design (per PRD PR5 scope):

* ``StructuredDiff`` is a thin factory. Static ``str`` sources return a
  ``box`` host element directly — no function component, no hooks. This
  matches the common case (diffs are usually rendered from snapshot
  strings, not live signals) and keeps the call cheap.
* Reactive sources (``Signal[str]`` / ``Callable[[], str]``) are
  deferred to a function component body (:func:`_DiffImpl`) so a signal
  write triggers a re-render through the parent's normal re-render
  machinery. The body re-runs ``unified_diff`` on every mount; PR5 does
  not memoise, mirroring :func:`Markdown`'s "re-parse the whole
  document" trade-off.
* Diff parsing walks the ``unified_diff`` output line-by-line. The
  ``+++`` / ``---`` file markers are emitted only when ``show_header``
  is set (we let ``difflib`` include them when needed and filter at
  render time so the same parser handles both modes). Hunk headers
  (``@@ ... @@``) are always rendered, in ``hunk_color``; ``+`` / ``-``
  bodies dispatch to :func:`_render_diff_line`; context lines render as
  plain ``Text`` in ``context_color``.
* Highlighted ``+`` / ``-`` lines split the row into a coloured prefix
  glyph (``+`` / ``-``) followed by a :func:`HighlightedCode` body for
  the code portion. We deliberately reuse ``HighlightedCode``'s own
  box-of-rows shape: when the body has no newline (the typical diff
  case) the rendered output is a single row whose inline tokens sit
  next to the prefix glyph inside a ``flexDirection="row"`` ``Box``.

Colour note: the PRD's example theme spelled the dim colour
``"brightBlack"``; PyInk's named-colour table
(see :data:`ink.render.ansi.NAMED_COLORS`) spells that ``"gray"`` /
``"grey"`` / ``"blackBright"``. We use ``"gray"`` for file-marker dim
treatment, matching :mod:`ink.externals.highlighted_code`.

PR5 scope: ships ``StructuredDiff`` only. Examples land in PR6.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from typing import Any

from ink.components.box import Box
from ink.components.text import Text
from ink.core.element import Element, create_element
from ink.core.signal import Signal
from ink.externals.divider import Divider

__all__ = ["StructuredDiff"]


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _resolve_source(source: str | Signal[str] | Callable[[], str]) -> str:
    """Return the current string carried by ``source``.

    Centralises the three-shape dispatch (``str`` / ``Signal[str]`` /
    ``Callable[[], str]``) so both the static fast path and the reactive
    function component can share the resolution logic. Mirrors the
    helpers in :mod:`ink.externals.streaming_text` and
    :mod:`ink.externals.markdown`.
    """
    if isinstance(source, Signal):
        return source.value
    if callable(source):
        return source()
    return source


# ---------------------------------------------------------------------------
# Diff line rendering
# ---------------------------------------------------------------------------


def _pygments_available() -> bool:
    """Return ``True`` if :mod:`pygments` is importable.

    Centralising the probe lets the static fast path and the reactive
    function component decide once per render whether to use
    :func:`HighlightedCode` for ``+`` / ``-`` bodies. We deliberately
    swallow ``ImportError`` rather than re-raising — diff rendering
    must not crash when the optional extra is absent.
    """
    try:
        import pygments  # noqa: F401
    except ImportError:
        return False
    return True


def _render_diff_line(
    line: str,
    *,
    color: str,
    language: str,
    prefix: str,
    use_highlight: bool,
    theme: dict[str, str | None] | None,
    bg_color: str | None = None,
) -> Element:
    """Render a single ``+`` / ``-`` diff row.

    Parameters
    ----------
    line:
        Full diff line including the ``+`` / ``-`` prefix.
    color:
        Colour spec applied to the prefix glyph (and to the body when
        highlighting is off).
    language:
        Pygments lexer alias forwarded to :func:`HighlightedCode` when
        highlighting is on. ``"text"`` skips highlighting even when
        Pygments is available.
    prefix:
        The single-character diff marker (``"+"`` / ``"-"``).
    use_highlight:
        Whether Pygments is both installed and the caller asked for a
        non-``text`` language. When ``False`` the whole line is emitted
        as a single coloured ``Text`` leaf.
    theme:
        Optional Pygments token colour override forwarded verbatim to
        :func:`HighlightedCode`.
    bg_color:
        Optional background colour spec applied to the row so the diff
        line visually fills the entire layout width with a coloured
        band — the strongest Claude Code ``StructuredDiff`` visual
        signature. ``None`` (default) preserves the legacy "no
        per-line background" behaviour. When set, ``Text`` is rendered
        with ``backgroundColor=bg_color`` and
        ``flushBackgroundToWidth=True`` so the bg spans the full
        terminal column width (not just the visible text cells).

    Returns
    -------
    Element
        Either a row ``Box`` (prefix + highlighted body) when
        highlighting is on, or a single coloured ``Text`` leaf
        otherwise.

    Notes
    -----
    The code portion is ``line[1:]`` — i.e. the diff line with its
    leading ``+`` / ``-`` stripped. We split prefix / body into two
    children of a row ``Box`` so the prefix keeps the diff colour
    (green / red) while the body inherits the syntax colours from
    :func:`HighlightedCode`. This matches the visual treatment of
    Claude Code's ``StructuredDiff``: the ``+`` / ``-`` glyph is a
    diff marker, the code next to it is source code.
    """
    code_part = line[1:]
    if not use_highlight:
        if bg_color:
            return Text(
                line,
                color=color,
                backgroundColor=bg_color,
                flushBackgroundToWidth=True,
            )
        return Text(line, color=color)

    # Lazy import: HighlightedCode itself imports pygments lazily, so
    # the only cost of this branch when pygments is missing is the
    # ``use_highlight`` flag (probed once per render via
    # :func:`_pygments_available``).
    from ink.externals.highlighted_code import HighlightedCode

    # When a per-line background is requested the prefix glyph carries
    # the bg + flushes it to the row width; HighlightedCode itself has
    # no ``backgroundColor`` prop on its inner Text leaves, so the
    # background only fills the prefix cell. This still produces the
    # signature coloured band for the prefix column — good enough for
    # the diff-row colour signature; a full-row fill would require
    # upstream changes to HighlightedCode (out of scope for PR2).
    if bg_color:
        return Box(
            Text(
                prefix,
                color=color,
                bold=True,
                backgroundColor=bg_color,
                flushBackgroundToWidth=True,
            ),
            HighlightedCode(code_part, language=language, theme=theme),
            flexDirection="row",
        )

    return Box(
        Text(prefix, color=color, bold=True),
        HighlightedCode(code_part, language=language, theme=theme),
        flexDirection="row",
    )


# ---------------------------------------------------------------------------
# Diff computation + rendering
# ---------------------------------------------------------------------------


def _compute_diff_lines(
    before: str,
    after: str,
    *,
    show_header: bool,
    context_lines: int,
) -> list[str]:
    """Run :func:`difflib.unified_diff` and return its output lines.

    ``show_header=False`` passes empty ``fromfile`` / ``tofile`` so
    ``difflib`` skips the ``---`` / ``+++`` file-marker lines entirely
    (an empty filename suppresses the marker per CPython's
    implementation). ``show_header=True`` uses the conventional
    ``"before"`` / ``"after"`` labels so the reader sees which side is
    which; the caller cannot customise these labels in PR5 (a future
    enhancement could expose ``fromfile`` / ``tofile`` props).
    """
    return list(
        difflib.unified_diff(
            before.splitlines(keepends=False),
            after.splitlines(keepends=False),
            fromfile="before" if show_header else "",
            tofile="after" if show_header else "",
            n=context_lines,
            lineterm="",
        )
    )


def _render_diff(
    before: str,
    after: str,
    *,
    language: str,
    context_lines: int,
    show_header: bool,
    show_add_count: bool,
    show_del_count: bool,
    add_color: str,
    del_color: str,
    hunk_color: str,
    context_color: str | None,
    highlight_theme: dict[str, str | None] | None,
    add_bg_color: str | None = None,
    del_bg_color: str | None = None,
) -> list[Element]:
    """Compute the diff and turn it into a list of row elements.

    Shared by both the static fast path and the reactive function
    component so the rendering logic lives in one place. Returns the
    body rows only (header / divider are appended by the caller so the
    reactive branch can re-use the same code).
    """
    diff_lines = _compute_diff_lines(
        before,
        after,
        show_header=show_header,
        context_lines=context_lines,
    )

    # Per-render highlight probe: ``language="text"`` always skips
    # highlighting regardless of pygments availability (mirrors
    # :func:`HighlightedCode`'s fast path).
    use_highlight = language not in ("text", "") and _pygments_available()

    # +/- counts exclude the ``+++`` / ``---`` file markers (those are
    # metadata, not content). Counted up-front so the header can show
    # them before the body.
    add_count = sum(
        1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
    )
    del_count = sum(
        1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
    )

    elements: list[Element] = []

    # Header: "Changes [+N -M]" (each piece opt-out). Yellow + bold so
    # the reader can scan diffs at a glance. Followed by a Divider for
    # visual separation between header and body.
    if show_header:
        header_parts = ["Changes"]
        if show_add_count:
            header_parts.append(f"+{add_count}")
        if show_del_count:
            header_parts.append(f"-{del_count}")
        elements.append(Text(" ".join(header_parts), bold=True, color="yellow"))
        elements.append(Divider())

    # Body: one Element per diff line.
    for line in diff_lines:
        if line.startswith("@@"):
            # Hunk header — always rendered in hunk_color, bold so it
            # stands out from the surrounding diff lines.
            elements.append(Text(line, color=hunk_color, bold=True))
        elif line.startswith("+++") or line.startswith("---"):
            # File markers only appear when show_header is on (we pass
            # empty filenames to difflib otherwise). Rendered dim so
            # they read as metadata, not content.
            elements.append(Text(line, dimColor=True))
        elif line.startswith("+"):
            elements.append(
                _render_diff_line(
                    line,
                    color=add_color,
                    language=language,
                    prefix="+",
                    use_highlight=use_highlight,
                    theme=highlight_theme,
                    bg_color=add_bg_color,
                )
            )
        elif line.startswith("-"):
            elements.append(
                _render_diff_line(
                    line,
                    color=del_color,
                    language=language,
                    prefix="-",
                    use_highlight=use_highlight,
                    theme=highlight_theme,
                    bg_color=del_bg_color,
                )
            )
        else:
            # Context line (leading space) or empty line — inherit the
            # terminal default unless ``context_color`` is set. Empty
            # diff lines (``""``) come from blank source rows; we still
            # emit a Text so the row count matches the diff.
            elements.append(Text(line, color=context_color))

    return elements


# ---------------------------------------------------------------------------
# Reactive function component
# ---------------------------------------------------------------------------


def _DiffImpl(**props: Any) -> Element:
    """Function component body for the reactive source branch.

    Runs inside the reconciler render context. Function components in
    PyInk only run **once on mount**; the reactivity model is that
    signals read *during layout* establish subscriptions, so for the
    diff tree to re-paint on a source ``Signal`` write we must read the
    signal inside a layout-time callable (same pattern
    :func:`ink.externals.markdown._MarkdownImpl` uses).

    We achieve this by returning a ``Box`` whose only child is a single
    ``Text`` leaf carrying a callable that, when invoked during layout:

    1. Resolves the current ``before`` / ``after`` source strings.
    2. Computes the diff via :func:`difflib.unified_diff`.
    3. Renders the diff into a tree of ``Element``\\ s.
    4. Lays that tree out via a throwaway :class:`Reconciler` at the
       active instance's column width and renders it to a string.

    The resulting string becomes the ``Text`` leaf's body. Because the
    signal read happens inside the layout-time callable, the render
    loop's tracking context picks up the subscription and re-paints on
    every write.
    """
    before: str | Signal[str] | Callable[[], str] = props["before"]
    after: str | Signal[str] | Callable[[], str] = props["after"]
    language: str = props["language"]
    context_lines: int = props["context_lines"]
    show_header: bool = props["show_header"]
    show_add_count: bool = props["show_add_count"]
    show_del_count: bool = props["show_del_count"]
    add_color: str = props["add_color"]
    del_color: str = props["del_color"]
    hunk_color: str = props["hunk_color"]
    context_color: str | None = props["context_color"]
    highlight_theme: dict[str, str | None] | None = props["highlight_theme"]
    add_bg_color: str | None = props.get("add_bg_color")
    del_bg_color: str | None = props.get("del_bg_color")
    box_props: dict[str, Any] = props["box_props"]

    from ink.core.reconciler import Reconciler
    from ink.hooks._runtime import _get_current_instance
    from ink.layout import layout, render_layout_to_string

    def render_reactive() -> str:
        # Resolve the sources at layout time so a Signal read here
        # establishes a subscription inside the render-loop effect's
        # tracking context.
        b = _resolve_source(before)
        a = _resolve_source(after)
        elements = _render_diff(
            b,
            a,
            language=language,
            context_lines=context_lines,
            show_header=show_header,
            show_add_count=show_add_count,
            show_del_count=show_del_count,
            add_color=add_color,
            del_color=del_color,
            hunk_color=hunk_color,
            context_color=context_color,
            highlight_theme=highlight_theme,
            add_bg_color=add_bg_color,
            del_bg_color=del_bg_color,
        )
        if not elements:
            return ""
        inner = create_element(
            "box", *elements, flexDirection="column"
        )
        reconciler = Reconciler()
        mounted = reconciler.mount(inner)
        try:
            inst = _get_current_instance()
            columns = 80
            if inst is not None:
                cols_attr = getattr(inst, "columns", 0)
                if isinstance(cols_attr, int) and cols_attr > 0:
                    columns = cols_attr
            tree = layout(mounted, columns=columns)
            return render_layout_to_string(tree)
        finally:
            reconciler.unmount(mounted)

    box_props = dict(box_props)
    box_props.pop("flexDirection", None)
    return Box(
        Text(render_reactive),
        flexDirection="column",
        **box_props,
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def StructuredDiff(
    before: str | Signal[str] | Callable[[], str],
    after: str | Signal[str] | Callable[[], str],
    *,
    language: str = "text",
    context_lines: int = 3,
    show_header: bool = True,
    show_add_count: bool = True,
    show_del_count: bool = True,
    add_color: str = "green",
    del_color: str = "red",
    hunk_color: str = "magenta",
    context_color: str | None = None,
    add_bg_color: str | None = None,
    del_bg_color: str | None = None,
    theme: dict[str, str | None] | None = None,
    **box_props: Any,
) -> Element:
    """Render a file-edit diff between two snapshots.

    Parameters
    ----------
    before:
        Old text. Three shapes are accepted (see module docstring):

        * ``str`` — static.
        * :class:`ink.Signal` ``[str]`` — reactive.
        * ``Callable[[], str]`` — evaluated lazily during layout.
    after:
        New text. Same three shapes as ``before``.
    language:
        Pygments lexer alias forwarded to :func:`HighlightedCode` for
        the ``+`` / ``-`` bodies (``"python"`` / ``"javascript"`` /
        ``"sql"`` / …). ``"text"`` (default) skips highlighting; the
        bodies are emitted as plain coloured ``Text`` leaves. When
        :mod:`pygments` is not installed, highlighting is silently
        disabled — diff rendering must not crash on a missing optional
        extra.
    context_lines:
        Number of unchanged context lines to show around each hunk.
        ``3`` (default) mirrors ``git diff``; ``0`` shows only changed
        lines (no context); larger values surface more surrounding code.
    show_header:
        When ``True`` (default), render a yellow bold header line
        (``Changes [+N -M]"``) followed by a :func:`Divider`. The
        ``N`` / ``M`` counts are controlled by ``show_add_count`` /
        ``show_del_count``.
    show_add_count:
        Include ``+N`` in the header (default ``True``).
    show_del_count:
        Include ``-M`` in the header (default ``True``).
    add_color:
        Colour spec for ``+`` line prefix (and body when highlighting
        is off). Defaults to ``"green"`` (SGR 32).
    del_color:
        Colour spec for ``-`` line prefix (and body when highlighting
        is off). Defaults to ``"red"`` (SGR 31).
    hunk_color:
        Colour spec for ``@@ ... @@`` hunk headers. Defaults to
        ``"magenta"`` (SGR 35).
    context_color:
        Colour spec for context lines (leading space). ``None``
        (default) inherits the terminal default.
    add_bg_color:
        Optional background colour spec for ``+`` lines. When set, each
        ``+`` row's ``Text`` is rendered with ``backgroundColor`` +
        ``flushBackgroundToWidth=True`` so the colour band fills the
        full layout width — Claude Code's strongest diff visual
        signature (the green / red per-row band). ``None`` (default)
        preserves the legacy "no per-line background" behaviour. The
        recommended value for CC alignment is ``"rgb(30,70,32)"`` (a
        dim green that keeps the bright-green fg readable).
    del_bg_color:
        Optional background colour spec for ``-`` lines. Same
        semantics as ``add_bg_color`` but applied to deletion rows.
        Recommended CC-aligned value: ``"rgb(74,32,32)"`` (dim red).
    theme:
        Optional Pygments token → colour mapping forwarded verbatim to
        :func:`HighlightedCode` when highlighting is on. ``None`` lets
        :func:`HighlightedCode` use its own
        :data:`~ink.externals.highlighted_code.DEFAULT_THEME`.
    **box_props:
        Forwarded to the outer ``Box`` container (``flexDirection`` is
        always set to ``"column"`` and cannot be overridden — the
        component's contract is "one row per diff line"). Useful props
        include ``borderStyle`` / ``padding`` / ``width`` /
        ``backgroundColor``.

    Returns
    -------
    Element
        The static fast path (``before`` and ``after`` both ``str``)
        returns a ``box`` host element directly — no function component,
        no hooks. The reactive branch (either source is a ``Signal`` or
        ``Callable``) returns an element whose ``type`` is
        :func:`_DiffImpl`, a function component that re-computes the
        diff on every mount.

    Usage
    -----
    Static::

        StructuredDiff(before, after, language="python")

    Reactive::

        before_sig = signal(before_text)
        after_sig = signal(after_text)
        StructuredDiff(before_sig, after_sig, language="python")
    """
    # Static fast path: both sources plain strings. No function
    # component, no hooks, no live render pipeline required. This is
    # the cheapest path and matches the common case.
    if isinstance(before, str) and isinstance(after, str):
        elements = _render_diff(
            before,
            after,
            language=language,
            context_lines=context_lines,
            show_header=show_header,
            show_add_count=show_add_count,
            show_del_count=show_del_count,
            add_color=add_color,
            del_color=del_color,
            hunk_color=hunk_color,
            context_color=context_color,
            highlight_theme=theme,
            add_bg_color=add_bg_color,
            del_bg_color=del_bg_color,
        )
        box_props = dict(box_props)
        box_props.pop("flexDirection", None)
        return Box(*elements, flexDirection="column", **box_props)

    # Reactive branch: defer to a function component so signal writes
    # re-render. The reconciler mounts it like any other function
    # component; the body re-computes the diff on every mount.
    return create_element(
        _DiffImpl,
        before=before,
        after=after,
        language=language,
        context_lines=context_lines,
        show_header=show_header,
        show_add_count=show_add_count,
        show_del_count=show_del_count,
        add_color=add_color,
        del_color=del_color,
        hunk_color=hunk_color,
        context_color=context_color,
        highlight_theme=theme,
        add_bg_color=add_bg_color,
        del_bg_color=del_bg_color,
        box_props=box_props,
    )
