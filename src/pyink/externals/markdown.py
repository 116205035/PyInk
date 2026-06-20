"""``Markdown`` — render Markdown source as PyInk elements (Phase 3 PR3).

Mirrors :mod:`ink-markdown` (which delegates to :mod:`marked-terminal`):
turn a CommonMark Markdown string into a column of styled ``Text`` /
``Box`` leaves, one per block. Inline markup (bold / italic / inline
code / links / line breaks) is flattened into a single string with
inline SGR sequences applied per span; that string is handed to a
single ``Text`` leaf so the parent's word-wrap machinery still works
end-to-end.

``markdown-it-py`` is an *optional* dependency. The factory ``import``\\ s
it lazily inside the function body; if the package is missing we raise
an ``ImportError`` whose message points the caller at the right extra
(``pip install pyink[markdown]``). Nothing in the rest of PyInk imports
``markdown_it``, so the optional group only matters when this component
is actually used.

Design (per PRD PR3 scope):

* ``Markdown`` is a thin factory that returns a ``box`` host element
  for static ``str`` sources — no function component, no hooks, no live
  render pipeline needed. This is the cheapest possible shape and
  matches the common case (Markdown is usually rendered from a snapshot
  string, not a live signal).
* Reactive sources (``Signal[str]`` / ``Callable[[], str]``) are
  deferred to a function component body (:func:`_MarkdownImpl`) so the
  signal write triggers a re-render through the parent's normal
  re-render machinery. The function component parses and renders the
  Markdown on every mount — the PRD's "Out of Scope" note explicitly
  defers incremental parsing, so re-parsing the whole document is the
  expected cost.
* Parsing uses :class:`markdown_it.MarkdownIt` configured with the
  ``"commonmark"`` preset plus the ``table`` plugin (the PRD's
  "supported Markdown elements" list calls out tables). The parser is
  constructed fresh per parse call so theme / config changes between
  renders are honoured without stale state.
* Block rendering is a token walker (:func:`_render_tokens`). Each
  block token type dispatches to a small helper that returns a single
  ``Element`` (a ``Text`` for paragraphs / headings, a ``Box`` for
  blockquotes / lists / fences / tables, a :func:`Divider` for
  ``hr``).
* Inline rendering (:func:`_render_inline`) walks the inline
  ``children`` of an ``inline`` token and concatenates plain strings
  with per-span SGR sequences applied via
  :func:`pyink.render.ansi.apply_style`. The concatenated string is
  handed to a single ``Text`` leaf so the parent's word-wrap pass sees
  one continuous run of text. The layout measure pass strips ANSI
  (CSI) sequences, so the extra SGR bytes do not inflate the column
  budget.

Inline code colour: the PRD example theme spells it ``"red"`` (SGR 31).
We follow that. Heading colours mirror the PRD's example theme, with
``"gray"`` (SGR 90) used in place of ``"brightBlack"`` because PyInk's
named-colour table (see :data:`pyink.render.ansi.NAMED_COLORS`) spells
it that way (see :mod:`pyink.externals.highlighted_code` for the same
choice).

Code-block integration (PR4): fenced / indented code blocks render via
:func:`HighlightedCode` when :mod:`pygments` is importable. The block is
wrapped in a single-line bordered ``Box`` so the reader can see where it
starts and stops. If :mod:`pygments` is missing (or the
``code_block_show_border`` theme knob disables the frame), we fall back
to the PR3 plain-text path: one dim ``Text`` per source line inside a
plain ``Box``. The ``code_block_theme`` knob is forwarded verbatim to
:func:`HighlightedCode`'s ``theme=`` prop so callers can override the
Pygments token colours used inside Markdown code blocks.

Link rendering: links are wrapped in OSC 8 sequences via the
:func:`pyink.externals.link._wrap_osc8` helper so a Markdown link
behaves identically to a hand-written :func:`Link`. We import the
helper rather than reimplementing it so the wrapping contract lives in
one place (per the code-reuse guide).

PR4 scope: this PR layers HighlightedCode integration on top of PR3.
``StructuredDiff`` is PR5, examples are PR6.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Signal
from pyink.externals.divider import Divider
from pyink.externals.link import _wrap_osc8
from pyink.render.ansi import apply_style

if TYPE_CHECKING:
    # Avoid a hard runtime dependency on markdown_it's types at import
    # time — this is only used for inline annotations.
    from markdown_it.token import Token

__all__ = ["Markdown", "DEFAULT_MARKDOWN_THEME"]

#: Default theme: per-block colour / weight hints. Keys mirror the PRD's
#: example theme. ``None`` means "inherit the terminal default". Colour
#: names use PyInk's :data:`pyink.render.ansi.NAMED_COLORS` vocabulary
#: (``"gray"`` instead of ``"brightBlack"`` — both spell SGR 90, but only
#: ``"gray"`` / ``"grey"`` / ``"blackBright"`` are in the table).
#:
#: ``h{n}_bold`` entries carry ``bool`` values; colour entries carry
#: ``str | None``. The dict is typed as ``dict[str, Any]`` so callers
#: can override any entry without hitting a union-narrowing error.
DEFAULT_MARKDOWN_THEME: dict[str, Any] = {
    "h1_color": "magenta",
    "h1_bold": True,
    "h2_color": "yellow",
    "h2_bold": True,
    "h3_color": "green",
    "h3_bold": True,
    "h4_color": "cyan",
    "h4_bold": True,
    "h5_color": "blue",
    "h5_bold": True,
    "h6_color": "gray",
    "h6_bold": True,
    "code_color": "red",
    "code_bg": None,
    "link_color": "blue",
    "quote_color": "gray",
    "code_block_lang_color": "gray",
    "hr_color": None,
    # ---- PR4: HighlightedCode integration knobs -------------------------
    # ``code_block_theme`` is forwarded verbatim to ``HighlightedCode``'s
    # ``theme=`` prop (a Pygments token → colour mapping). ``None`` lets
    # ``HighlightedCode`` use its own :data:`DEFAULT_THEME`.
    "code_block_theme": None,
    # Border colour of the code-block wrapper Box (only applied when
    # ``code_block_show_border`` is true and pygments is available so
    # HighlightedCode is in use). Default dim gray matches the visual
    # treatment of the language header / quote colour.
    "code_block_border_color": "gray",
    # Whether to draw a single-line border around the code block when
    # HighlightedCode is in use. When ``False``, the block sits inline
    # without a frame (the PR3 fallback path always omits the frame).
    "code_block_show_border": True,
    # Whether to surface the language label as a dim header line above
    # the highlighted code. Forwarded to both paths so the header stays
    # consistent between highlighted and fallback rendering.
    "code_block_show_language": True,
}


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _resolve_source(
    source: str | Signal[str] | Callable[[], str],
) -> str:
    """Return the current string carried by ``source``.

    Centralises the three-shape dispatch (``str`` / ``Signal[str]`` /
    ``Callable[[], str]``) so both the static fast path and the reactive
    function component can share the resolution logic.
    """
    if isinstance(source, Signal):
        return source.value
    if callable(source):
        return source()
    return source


# Module-level parser cache. ``MarkdownIt`` construction is non-trivial
# (it builds the rule pipeline + table plugin every call); reusing a
# single process-wide instance is safe because ``MarkdownIt.parse`` is
# stateless across calls (the per-call state lives on the parser's
# ``StateBlock``, not on the parser itself).
_PARSER: Any = None


def _get_parser() -> Any:
    """Return a process-wide ``MarkdownIt`` instance.

    Construction goes through the ``"commonmark"`` preset + the ``table``
    plugin. Reusing a single instance avoids re-building the rule pipeline
    on every render, which was a significant contributor to the Phase 3
    "streaming Markdown pins CPU" bug: the streaming demo drips ~50
    characters/sec into the buffer, each write re-parses the whole
    document, and the per-parse cost is dominated by parser setup.
    """
    global _PARSER
    if _PARSER is None:
        try:
            from markdown_it import MarkdownIt
        except ImportError as exc:
            raise ImportError(
                "Markdown requires markdown-it-py. "
                "Install: pip install pyink[markdown]"
            ) from exc
        _PARSER = MarkdownIt("commonmark").enable("table")
    return _PARSER


def _parse(text: str) -> list[Token]:
    """Parse ``text`` into a list of markdown_it ``Token``\\ s.

    Uses the shared process-wide parser (see :func:`_get_parser`).
    """
    tokens = _get_parser().parse(text)
    return list(tokens)


def _merge_theme(theme: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of :data:`DEFAULT_MARKDOWN_THEME` overlaid with ``theme``.

    ``theme=None`` returns the defaults unchanged. Non-``None`` values
    in ``theme`` win; ``None`` values are honoured as "reset to the
    terminal default" (matching the convention used by
    :data:`pyink.externals.highlighted_code.DEFAULT_THEME`).
    """
    effective: dict[str, Any] = dict(DEFAULT_MARKDOWN_THEME)
    if theme:
        effective.update(theme)
    return effective


def _theme_bool(value: Any) -> bool:
    """Normalise a theme boolean entry.

    The default theme stores Python ``bool``\\ s; user-supplied themes
    may pass strings (``"True"`` / ``"False"``) or ints. Anything that
    isn't recognisably falsy becomes ``True`` so a ``"True"`` string
    from a config file still enables bold headings.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    if isinstance(value, (int, float)):
        return bool(value)
    return value is not None


# ---------------------------------------------------------------------------
# Inline rendering
# ---------------------------------------------------------------------------


def _link_url(token: Token) -> str:
    """Pull the URL out of a ``link_open`` token's ``attrs``.

    markdown_it stores link targets as ``{"href": "..."}`` (optionally
    ``title``); ``attrs`` is a plain dict. Missing ``href`` falls back
    to the empty string so a malformed link still renders as a styled
    span without crashing the whole block.
    """
    attrs = token.attrs or {}
    href = attrs.get("href", "")
    if isinstance(href, str):
        return href
    return ""


def _collect_balanced(
    children: list[Token],
    start: int,
    open_type: str,
    close_type: str,
) -> tuple[list[Token], int]:
    """Return the children of a balanced open / close pair.

    Walks ``children`` from ``start + 1`` and tracks nesting depth for
    ``open_type`` / ``close_type`` until depth hits zero. Returns the
    inner token list and the index of the matching close token. Used
    for ``strong`` / ``em`` / ``s`` / ``link`` spans, which markdown_it
    always emits as balanced pairs.
    """
    depth = 1
    j = start + 1
    inner: list[Token] = []
    while j < len(children):
        nested = children[j]
        if nested.type == open_type:
            depth += 1
        elif nested.type == close_type:
            depth -= 1
            if depth == 0:
                return inner, j
        inner.append(nested)
        j += 1
    # Unbalanced (shouldn't happen with well-formed Markdown); return
    # whatever we collected.
    return inner, j


def _render_inline(
    children: list[Token],
    theme: dict[str, Any],
    *,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    link_url: str | None = None,
    link_color: str | None = None,
) -> str:
    """Render a list of inline ``Token``\\ s into a single styled string.

    The walk preserves nesting: a ``strong_open`` flips ``bold=True``
    for everything until the matching ``strong_close``, and so on for
    emphasis / strikethrough. Plain ``text`` runs are wrapped in SGR
    sequences via :func:`apply_style` for the active style combination,
    then concatenated. The layout measure pass strips ANSI so the
    extra bytes do not affect the column budget.

    The theme's ``__quote__`` flag (set by :func:`_render_blockquote`)
    enables ``dimColor=True`` on every inline run so the whole quote
    reads as muted text without us having to thread a parameter
    through every recursion level.

    Inline leaves handled here:

    * ``text`` — verbatim text run with the active style.
    * ``code_inline`` — single run with the ``code_color`` from the
      theme. Inline code never inherits bold / italic / strikethrough
      from the surrounding context (it's a distinct typographic style).
    * ``softbreak`` / ``hardbreak`` — newline character; the parent
      ``Text`` treats ``\\n`` as a line break inside the same leaf.
    * ``link_open`` / ``link_close`` — wraps the contained text in an
      OSC 8 sequence (via :func:`_wrap_osc8`), with ``link_color``
      applied to the visible text. Nested styling inside a link is
      honoured (e.g. ``**[bold link](url)**`` works).
    """
    out: list[str] = []
    quote_dim = bool(theme.get("__quote__"))

    i = 0
    while i < len(children):
        child = children[i]
        ctype = child.type

        if ctype == "text":
            text = child.content
            if link_url is not None:
                # Inside a link: wrap in OSC 8 with link color applied.
                color = link_color
                styled = apply_style(
                    text,
                    color=color,
                    bold=bold,
                    italic=italic,
                    strikethrough=strikethrough,
                    dimColor=quote_dim,
                )
                out.append(_wrap_osc8(styled, link_url))
            else:
                out.append(
                    apply_style(
                        text,
                        bold=bold,
                        italic=italic,
                        strikethrough=strikethrough,
                        dimColor=quote_dim,
                    )
                )
            i += 1
            continue

        if ctype == "code_inline":
            color = theme.get("code_color")
            out.append(apply_style(child.content, color=color))
            i += 1
            continue

        if ctype in ("softbreak", "hardbreak"):
            out.append("\n")
            i += 1
            continue

        if ctype == "link_open":
            url = _link_url(child)
            inner, j = _collect_balanced(children, i, "link_open", "link_close")
            inner_str = _render_inline(
                inner,
                theme,
                bold=bold,
                italic=italic,
                strikethrough=strikethrough,
                link_url=url,
                link_color=theme.get("link_color"),
            )
            out.append(inner_str)
            i = j + 1
            continue

        if ctype == "strong_open":
            inner, j = _collect_balanced(children, i, "strong_open", "strong_close")
            out.append(
                _render_inline(
                    inner,
                    theme,
                    bold=True,
                    italic=italic,
                    strikethrough=strikethrough,
                    link_url=link_url,
                    link_color=link_color,
                )
            )
            i = j + 1
            continue

        if ctype == "em_open":
            inner, j = _collect_balanced(children, i, "em_open", "em_close")
            out.append(
                _render_inline(
                    inner,
                    theme,
                    bold=bold,
                    italic=True,
                    strikethrough=strikethrough,
                    link_url=link_url,
                    link_color=link_color,
                )
            )
            i = j + 1
            continue

        if ctype == "s_open":
            inner, j = _collect_balanced(children, i, "s_open", "s_close")
            out.append(
                _render_inline(
                    inner,
                    theme,
                    bold=bold,
                    italic=italic,
                    strikethrough=True,
                    link_url=link_url,
                    link_color=link_color,
                )
            )
            i = j + 1
            continue

        # Unknown inline token types (image, html_inline, …) are emitted
        # as plain text content when present, otherwise dropped. This
        # keeps the renderer forward-compatible with markdown_it plugins
        # that add token types we don't yet model.
        if child.content:
            out.append(child.content)
        i += 1

    return "".join(out)


def _render_inline_token(
    token: Token,
    theme: dict[str, Any],
) -> str:
    """Render an ``inline`` token's children into a single styled string."""
    return _render_inline(token.children or [], theme)


def _render_heading(
    token: Token,
    inline: Token,
    theme: dict[str, Any],
) -> Element:
    """Render a heading (``h1``-``h6``) as a coloured, bold ``Text`` leaf.

    ``token.tag`` carries the level (``"h1"`` … ``"h6"``); we look up
    ``h{n}_color`` / ``h{n}_bold`` in the theme and build the props
    accordingly. A missing colour falls through to the terminal default.
    Inline content inside a heading is rendered with the heading's
    colour and bold settings overlaid on top of any inline styling
    (bold wins).
    """
    level = token.tag  # "h1" / "h2" / … / "h6"
    suffix = level[1:]
    color = theme.get(f"h{suffix}_color")
    bold = _theme_bool(theme.get(f"h{suffix}_bold", True))

    # Render the inline content first, then wrap the whole heading in
    # the heading colour / bold so headings read uniformly. We compose
    # by applying the heading's style to the already-styled inline
    # segments — apply_style on the joined string would double-wrap
    # any segment that already had bold, which is fine because SGR is
    # idempotent in practice.
    inline_str = _render_inline_token(inline, theme)
    styled = apply_style(inline_str, color=color, bold=bold)
    return Text(styled)


def _render_paragraph(inline: Token, theme: dict[str, Any]) -> Element:
    """Render a paragraph (``p``) as a plain ``Text`` leaf."""
    return Text(_render_inline_token(inline, theme))


def _render_fence(
    token: Token,
    theme: dict[str, Any],
) -> Element:
    """Render a fenced / indented code block.

    Two paths:

    * **Highlighted (PR4)** — when :mod:`pygments` is importable, the
      block renders via :func:`HighlightedCode` so the syntax is
      colourised. The language label (``token.info``, e.g. ``"python"``)
      is surfaced as a dim header line above the block when
      ``code_block_show_language`` is true, and the whole block is
      wrapped in a single-line bordered ``Box`` when
      ``code_block_show_border`` is true. ``code_block_theme`` is
      forwarded to :func:`HighlightedCode`'s ``theme=`` prop.
    * **Fallback (PR3)** — when :mod:`pygments` is missing, each source
      line becomes a single dim ``Text`` row inside a plain ``Box``.
      This keeps code blocks readable without the optional dependency.

    A trailing newline in ``token.content`` is stripped in both paths so
    the block doesn't render a blank bottom row (markdown_it emits the
    source verbatim including the newline before the closing fence).
    """
    code = token.content
    # Drop a single trailing newline so a fenced block doesn't render a
    # blank bottom row — markdown_it always emits the source verbatim
    # including the trailing newline before the closing fence.
    if code.endswith("\n"):
        code = code[:-1]
    info = token.info.strip() if token.info else ""
    show_language = _theme_bool(theme.get("code_block_show_language", True))

    # Build the optional language header up front — both paths share it
    # so the header stays visually consistent regardless of which
    # rendering path is taken.
    header: list[Element] = []
    lang_color = theme.get("code_block_lang_color")
    if info and show_language:
        header_props: dict[str, Any] = {"dimColor": True}
        if lang_color is not None:
            header_props["color"] = lang_color
        header.append(Text(info, **header_props))

    # Try the highlighted path first. If pygments isn't installed
    # (ImportError on the lazy import inside HighlightedCode), fall back
    # to the plain-text path. HighlightedCode is the single source of
    # truth for "is pygments available" — we don't duplicate the check
    # here, we just observe whatever it decides. This means a future
    # change to HighlightedCode's availability logic propagates for
    # free (per the code-reuse guide's "single source of truth" rule).
    try:
        from pyink.externals.highlighted_code import HighlightedCode
        highlighted = HighlightedCode(
            code,
            language=info or "text",
            theme=theme.get("code_block_theme"),
        )
    except ImportError:
        # PR3 fallback: plain dim Text per line. HighlightedCode raises
        # ImportError specifically when pygments is missing, which is
        # the only condition we want to catch here — any other error
        # from HighlightedCode should propagate.
        body = [Text(line, dimColor=True) for line in code.split("\n")]
        return Box(*header, *body, flexDirection="column")

    # Wrap the highlighted code in a bordered Box when the theme asks
    # for one. The header (if any) sits above the border so the language
    # label reads as a title rather than a content row.
    show_border = _theme_bool(theme.get("code_block_show_border", True))
    if show_border:
        border_color = theme.get("code_block_border_color")
        border_props: dict[str, Any] = {"borderStyle": "single"}
        if border_color is not None:
            border_props["borderColor"] = border_color
        return Box(
            *header,
            Box(highlighted, **border_props),
            flexDirection="column",
        )
    return Box(*header, highlighted, flexDirection="column")


def _render_blockquote(
    tokens: list[Token],
    start: int,
    theme: dict[str, Any],
) -> tuple[Element, int]:
    """Render a ``blockquote_open`` ... ``blockquote_close`` span.

    Returns the rendered ``Box`` and the index just past the matching
    ``blockquote_close``. The contents are rendered as nested blocks
    (typically paragraphs), then wrapped in a single ``Box`` with
    ``paddingLeft=2`` for the visual indent. The quote colour from
    the theme is applied to the wrapper's inline content by injecting
    a ``quote_dim`` flag into the inner render so each inline text
    run picks up ``dimColor=True``.
    """
    inner_tokens, j = _collect_balanced(tokens, start, "blockquote_open", "blockquote_close")
    quote_theme = dict(theme)
    quote_theme["__quote__"] = True
    inner_elements, _ = _render_tokens(inner_tokens, 0, quote_theme)
    return (
        Box(*inner_elements, flexDirection="column", paddingLeft=2),
        j + 1,
    )


def _render_list(
    tokens: list[Token],
    start: int,
    theme: dict[str, Any],
) -> tuple[Element, int]:
    """Render a ``bullet_list_open`` / ``ordered_list_open`` span.

    Each ``list_item_open`` ... ``list_item_close`` becomes one column
    row. The marker is a dim ``"-"`` (bullet) or ``"N."`` (ordered,
    starting at the item's ``info`` number or 1). The item's first
    block (typically a paragraph) renders inline with the marker; any
    subsequent blocks (nested lists, code blocks, …) render below,
    indented by ``paddingLeft``.
    """
    list_token = tokens[start]
    is_ordered = list_token.type == "ordered_list_open"
    close_type = list_token.type.replace("_open", "_close")

    # Collect the list body up to the matching close at depth 0.
    body_tokens, end = _collect_balanced(
        tokens, start, list_token.type, close_type
    )

    rows: list[Element] = []
    counter = 1
    i = 0
    while i < len(body_tokens):
        t = body_tokens[i]
        if t.type == "list_item_open":
            if is_ordered and t.info:
                with contextlib.suppress(ValueError):
                    counter = int(t.info)
            item_tokens, k = _collect_balanced(
                body_tokens, i, "list_item_open", "list_item_close"
            )
            rows.append(_render_list_item(item_tokens, is_ordered, counter, theme))
            if is_ordered:
                counter += 1
            i = k + 1
            continue
        i += 1

    return Box(*rows, flexDirection="column"), end + 1


def _render_list_item(
    tokens: list[Token],
    is_ordered: bool,
    counter: int,
    theme: dict[str, Any],
) -> Element:
    """Render a single list item.

    The first block in the item is rendered inline with the marker
    (so ``- a`` reads as one line). Subsequent blocks are wrapped in a
    nested column ``Box`` with ``paddingLeft=2`` so nested lists and
    multi-paragraph items indent under the marker.
    """
    marker = f"{counter}." if is_ordered else "-"
    if not tokens:
        return Box(Text(f"{marker} ", dimColor=True), flexDirection="row")

    # Split off the first paragraph (if any) for inline rendering with
    # the marker. Other leading blocks (nested lists, code blocks, …)
    # go straight to the indented column.
    first_inline: Token | None = None
    rest_start = 0
    if (
        len(tokens) >= 3
        and tokens[0].type == "paragraph_open"
        and tokens[1].type == "inline"
        and tokens[2].type == "paragraph_close"
    ):
        first_inline = tokens[1]
        rest_start = 3

    rest_tokens = tokens[rest_start:]
    rest_elements, _ = _render_tokens(rest_tokens, 0, theme)

    head_row_children: list[Element] = [
        Text(f"{marker} ", dimColor=True),
    ]
    if first_inline is not None:
        head_row_children.append(_render_paragraph(first_inline, theme))

    parts: list[Element] = [Box(*head_row_children, flexDirection="row", flexWrap="wrap")]
    if rest_elements:
        parts.append(
            Box(*rest_elements, flexDirection="column", paddingLeft=2)
        )
    return Box(*parts, flexDirection="column")


def _render_table(
    tokens: list[Token],
    start: int,
    theme: dict[str, Any],
) -> tuple[Element, int]:
    """Render a ``table_open`` ... ``table_close`` span as aligned columns.

    CommonMark tables come as ``thead_open`` (one row of ``th``) +
    ``tbody_open`` (rows of ``td``). We collect all cells, compute the
    max column width, and lay each row out as a row ``Box`` of padded
    cell ``Text`` leaves.
    """
    body_tokens, end = _collect_balanced(tokens, start, "table_open", "table_close")

    rows: list[list[str]] = []
    current_row: list[str] = []
    i = 0
    while i < len(body_tokens):
        t = body_tokens[i]
        if t.type == "tr_open":
            current_row = []
        elif t.type == "tr_close":
            rows.append(current_row)
        elif t.type in ("th_open", "td_open"):
            k = i + 1
            cell_text = ""
            while k < len(body_tokens) and body_tokens[k].type not in (
                "th_close",
                "td_close",
            ):
                if body_tokens[k].type == "inline":
                    cell_text = _inline_plain_text(body_tokens[k])
                k += 1
            current_row.append(cell_text)
            i = k
        i += 1

    if not rows:
        return Box(flexDirection="column"), end + 1

    # Column count from the widest row.
    n_cols = max(len(r) for r in rows)
    widths = [0] * n_cols
    for r in rows:
        for idx, cell in enumerate(r):
            widths[idx] = max(widths[idx], len(cell))

    rendered_rows: list[Element] = []
    for r in rows:
        cells: list[Element] = []
        for idx in range(n_cols):
            text = r[idx] if idx < len(r) else ""
            # Pad to the column width and add a one-space gutter so
            # adjacent cells don't run together when both fill their
            # column.
            padding = " " * (widths[idx] - len(text))
            gutter = " " if idx < n_cols - 1 else ""
            cells.append(Text(text + padding + gutter))
        rendered_rows.append(Box(*cells, flexDirection="row"))

    return Box(*rendered_rows, flexDirection="column"), end + 1


def _inline_plain_text(token: Token) -> str:
    """Flatten an ``inline`` token's children into a single plain string.

    Used by table cell rendering — we don't honour inline styling inside
    table cells in PR3 (the PRD calls out only "basic table support").
    """
    out: list[str] = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            out.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            out.append(" ")
    return "".join(out)


# ---------------------------------------------------------------------------
# Block rendering: token walker
# ---------------------------------------------------------------------------


def _render_tokens(
    tokens: list[Token],
    start: int,
    theme: dict[str, Any],
) -> tuple[list[Element], int]:
    """Walk ``tokens`` from ``start`` and render each top-level block.

    Returns the list of rendered ``Element``\\ s and the index of the
    next unprocessed token (which is ``len(tokens)`` when called at the
    top level, or the index past the closing token of a sub-block when
    called recursively).
    """
    elements: list[Element] = []
    i = start
    while i < len(tokens):
        token = tokens[i]
        ttype = token.type

        if ttype == "heading_open":
            # heading_open / inline / heading_close
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            if inline is not None and inline.type == "inline":
                elements.append(_render_heading(token, inline, theme))
            # Skip heading_open + inline + heading_close.
            i += 3
            continue

        if ttype == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            if inline is not None and inline.type == "inline":
                elements.append(_render_paragraph(inline, theme))
            i += 3
            continue

        if ttype in ("fence", "code_block"):
            elements.append(_render_fence(token, theme))
            i += 1
            continue

        if ttype == "blockquote_open":
            el, next_i = _render_blockquote(tokens, i, theme)
            elements.append(el)
            i = next_i
            continue

        if ttype in ("bullet_list_open", "ordered_list_open"):
            el, next_i = _render_list(tokens, i, theme)
            elements.append(el)
            i = next_i
            continue

        if ttype == "hr":
            hr_color = theme.get("hr_color")
            elements.append(Divider(color=hr_color))
            i += 1
            continue

        if ttype == "table_open":
            el, next_i = _render_table(tokens, i, theme)
            elements.append(el)
            i = next_i
            continue

        if ttype == "inline":
            # A bare inline token at the top level (no paragraph wrapper).
            elements.append(_render_paragraph(token, theme))
            i += 1
            continue

        # Anything else (html_block, paragraph_close, list_item_close,
        # …) is structural noise from a sub-block — skip silently.
        i += 1

    return elements, i


# ---------------------------------------------------------------------------
# Reactive function component
# ---------------------------------------------------------------------------


def _render_markdown_to_string(text: str, columns: int, theme: dict[str, Any]) -> str:
    """Render a Markdown source string to a flat styled string.

    Centralised so the static fast path (:func:`Markdown`) and the
    reactive component (:func:`_MarkdownImpl`) share the same
    parse-render-snapshot pipeline. The result is a single string
    carrying inline SGR sequences (and OSC 8 link wrappers) that a
    ``Text`` leaf can hand to the layout engine.

    A throwaway :class:`Reconciler` mounts the per-block ``box`` tree
    so we can run ``layout`` + ``render_layout_to_string`` on it. This
    mirrors the pattern :func:`Link` / :func:`Transform` use to render
    a sub-tree to a snapshot string; the throwaway scope contains any
    hooks the blocks might establish.
    """
    from pyink.core.reconciler import Reconciler
    from pyink.layout import layout, render_layout_to_string

    tokens = _parse(text)
    elements, _ = _render_tokens(tokens, 0, theme)
    inner = create_element("box", *elements, flexDirection="column", gap=1)
    reconciler = Reconciler()
    mounted = reconciler.mount(inner)
    try:
        tree = layout(mounted, columns=columns)
        return render_layout_to_string(tree)
    finally:
        reconciler.unmount(mounted)


#: Rendered-string cache. Keyed on ``(text, columns, theme_id)``. The
#: render-loop's subscription layout *and* the paint layout both evaluate
#: the reactive ``Text`` callable, so without a cache each signal flush
#: re-parses the Markdown and re-lays-out the per-block tree twice. For
#: the streaming demo (≈50 writes/sec) that pinned a core at 100% CPU.
#:
#: The cache is bounded (LRU, ``_RENDER_CACHE_MAX`` entries). Theme is a
#: mutable ``dict`` so we key on its ``id()`` — themes are built once per
#: ``Markdown(...)`` call site and reused across renders, which makes
#: ``id(theme)`` a stable identity within a streaming session.
_RENDER_CACHE_MAX: int = 64
_render_cache: dict[tuple[str, int, int], str] = {}


def _cached_render(text: str, columns: int, theme: dict[str, Any]) -> str:
    """LRU-cached wrapper around :func:`_render_markdown_to_string`.

    Returns the cached string when ``(text, columns, theme_id)`` was
    rendered recently; otherwise computes, caches and returns it.
    """
    key = (text, columns, id(theme))
    cached = _render_cache.get(key)
    if cached is not None:
        # Move-to-end so the LRU eviction order reflects recent use.
        _render_cache.pop(key)
        _render_cache[key] = cached
        return cached
    value = _render_markdown_to_string(text, columns, theme)
    _render_cache[key] = value
    if len(_render_cache) > _RENDER_CACHE_MAX:
        # Evict the oldest entry (first inserted).
        _render_cache.pop(next(iter(_render_cache)))
    return value


def _MarkdownImpl(**props: Any) -> Element:
    """Function component body for the reactive source branch.

    Runs inside the reconciler render context. Function components in
    PyInk only run **once on mount**; the reactivity model is that
    signals read *during layout* establish subscriptions, so for the
    Markdown tree to re-paint on a source ``Signal`` write we must
    read the signal inside a layout-time callable.

    We achieve this by returning a ``Box`` whose only child is a single
    ``Text`` leaf carrying a callable that, when invoked during layout:

    1. Resolves the current source string (``Signal.value`` / callable /
       ``str``).
    2. Renders the Markdown into a styled string via
       :func:`_cached_render` (which parses, lays out a throwaway tree,
       and memoises the result so the render-loop's double-layout does
       not double the work).

    The resulting string becomes the ``Text`` leaf's body. Because the
    signal read happens inside the layout-time callable, the render
    loop's tracking context picks up the subscription and re-paints on
    every write. This mirrors the pattern
    :func:`pyink.components.Transform` uses to capture a snapshot of a
    sub-tree at mount time; here we use it at layout time so the
    snapshot refreshes whenever the source signal writes.
    """
    source: str | Signal[str] | Callable[[], str] = props["source"]
    theme: dict[str, Any] = props["theme"]
    box_props: dict[str, Any] = props["box_props"]

    from pyink.hooks._runtime import _get_current_instance

    def render_reactive() -> str:
        # Resolve the source at layout time so a Signal read here
        # establishes a subscription inside the render-loop effect's
        # tracking context.
        text = _resolve_source(source)
        if not text:
            return ""
        inst = _get_current_instance()
        columns = 80
        if inst is not None:
            cols_attr = getattr(inst, "columns", 0)
            if isinstance(cols_attr, int) and cols_attr > 0:
                columns = cols_attr
        return _cached_render(text, columns, theme)

    box_props = dict(box_props)
    box_props.pop("flexDirection", None)
    return Box(
        Text(render_reactive),
        flexDirection="column",
        gap=1,
        **box_props,
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def Markdown(
    source: str | Signal[str] | Callable[[], str],
    *,
    theme: dict[str, Any] | None = None,
    **box_props: Any,
) -> Element:
    """Render Markdown source as a column of PyInk elements.

    Parameters
    ----------
    source:
        Markdown source. Three shapes are accepted (see module docstring):

        * ``str`` — static. Parsed and rendered eagerly; the returned
          element is a plain ``box`` host (no function component, no
          hooks).
        * :class:`pyink.Signal` ``[str]`` — reactive. Each ``.value``
          write re-renders the surrounding component because the
          function component body subscribes to the signal.
        * ``Callable[[], str]`` — evaluated lazily during layout, like
          any other callable ``Text`` child. Re-renders when the parent
          re-renders.

    theme:
        Override (or extend) :data:`DEFAULT_MARKDOWN_THEME`. Keys are
        block-type / colour names (``"h1_color"``, ``"h2_bold"``,
        ``"code_color"``, ``"link_color"``, ``"quote_color"``, …). A
        value of ``None`` resets the entry to the terminal default.
    **box_props:
        Forwarded to the outer ``Box`` container (``flexDirection`` is
        always set to ``"column"`` and cannot be overridden — the
        component's contract is "one block per row"). Useful props
        include ``borderStyle`` / ``padding`` / ``width``.

    Returns
    -------
    Element
        The static ``str`` fast path returns a ``box`` host element
        directly — no function component, no hooks. The reactive branch
        (``Signal`` / ``Callable``) returns an element whose ``type`` is
        :func:`_MarkdownImpl`, a function component that re-parses and
        re-renders the source on every mount.

    Raises
    ------
    ImportError
        If :mod:`markdown_it` is not installed. The error message
        points the caller at ``pip install pyink[markdown]``.

    Supported Markdown elements
    ---------------------------
    * Headings (``h1``-``h6``) with per-level colour + bold.
    * Paragraphs (plain ``Text`` leaves).
    * Inline emphasis (bold / italic / strikethrough — the last needs
      ``mdit-py-plugins`` or a Markdown-it preset that enables it).
    * Inline code (single-colour ``Text`` segment).
    * Links (rendered via OSC 8 hyperlinks; see
      :func:`pyink.externals.link._wrap_osc8`).
    * Ordered / unordered lists (with nesting and per-item markers).
    * Code blocks (rendered via :func:`HighlightedCode` when
      :mod:`pygments` is installed; plain dim ``Text`` rows otherwise).
      The code-block frame, language header, and Pygments theme are
      tunable via the ``code_block_show_border`` /
      ``code_block_show_language`` / ``code_block_theme`` /
      ``code_block_border_color`` / ``code_block_lang_color`` theme
      knobs.
    * Blockquotes (indented + dim).
    * Horizontal rules (via :func:`Divider`).
    * Tables (basic column alignment, no inline styling).
    * Soft / hard line breaks inside paragraphs.

    Usage
    -----
    ::

        Markdown("# Title\\n\\nSome **bold** text.")
        Markdown(my_signal_buffer)
        Markdown(source, theme={"h1_color": "cyan"})
    """
    effective_theme = _merge_theme(theme)

    # Static fast path: parse + render eagerly. No function component,
    # no hooks, no live render pipeline required.
    if isinstance(source, str):
        # Eagerly verify the optional dependency is installed so the
        # caller gets a clear error at call time rather than at first
        # render. The actual parse delegates to :func:`_parse`, which
        # re-checks the import — the redundant guard is intentional
        # (matches :func:`HighlightedCode`'s pattern).
        try:
            from markdown_it import MarkdownIt  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Markdown requires markdown-it-py. "
                "Install: pip install pyink[markdown]"
            ) from exc
        tokens = _parse(source)
        elements, _ = _render_tokens(tokens, 0, effective_theme)
        box_props = dict(box_props)
        box_props.pop("flexDirection", None)
        return Box(*elements, flexDirection="column", gap=1, **box_props)

    # Reactive branch: defer to a function component so signal writes
    # re-render. The reconciler mounts it like any other function
    # component; the body parses + renders on every mount.
    return create_element(
        _MarkdownImpl,
        source=source,
        theme=effective_theme,
        box_props=box_props,
    )
