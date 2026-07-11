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
(``pip install ink[markdown]``). Nothing in the rest of PyInk imports
``markdown_it``, so the optional group only matters when this component
is actually used.

Design (per PRD scope):

* ``Markdown`` routes both static (``str``) and reactive
  (``Signal[str]`` / ``Callable[[], str]``) sources through a single
  function component (:func:`_MarkdownImpl`) whose body returns a
  ``Box`` wrapping a single ``Text`` leaf carrying a layout-time
  callable. The callable parses + renders the Markdown when the layout
  pass invokes it, which is the only point at which the available
  content width is known — this lets width-aware blocks (tables) pick
  up the width via ``get_current_text_width()`` and responsively shrink
  or degrade to a key-value layout. PR2 unified the two paths; pre-PR2
  the static path eagerly built a ``box`` host element, which meant
  tables had no width context and couldn't shrink.
* The function component parses and renders the Markdown on every
  mount — the PRD's "Out of Scope" note explicitly defers incremental
  parsing, so re-parsing the whole document is the expected cost. The
  rendered string is cached (see :func:`_cached_render`) so the
  render-loop's double-layout doesn't double the work.
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
  :func:`ink.render.ansi.apply_style`. The concatenated string is
  handed to a single ``Text`` leaf so the parent's word-wrap pass sees
  one continuous run of text. The layout measure pass strips ANSI
  (CSI) sequences, so the extra SGR bytes do not inflate the column
  budget.

Inline code colour: PR3 defaults to the semantic ``"accent"`` key
(resolved to ``"cyan"``, SGR 36). Heading colours PR3-default to
``None`` (terminal default text colour) with h1 distinguished by
italic + underline; pre-PR3 used a rainbow palette (magenta / yellow /
green / cyan / blue / gray). A caller can restore the pre-PR3 look
via ``theme={"h1_color": "magenta", "code_color": "red", ...}`` —
see ``CHANGELOG.md`` for the full migration table.

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
:func:`ink.externals.link._wrap_osc8` helper so a Markdown link
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

from ink.components.box import Box
from ink.components.text import Text
from ink.core.element import Element, create_element
from ink.core.signal import Signal
from ink.externals.divider import Divider
from ink.externals.link import _wrap_osc8
from ink.layout.measure import WrapMode, string_width, wrap_text
from ink.render.ansi import BORDER_STYLES, apply_style

if TYPE_CHECKING:
    # Avoid a hard runtime dependency on markdown_it's types at import
    # time — this is only used for inline annotations.
    from markdown_it.token import Token

__all__ = ["Markdown", "DEFAULT_MARKDOWN_THEME"]

#: Semantic colour key → concrete colour name mapping.
#:
#: PR1 introduces a semantic colour layer so callers can say "accent" /
#: "muted" / "border" instead of hard-coding ``"cyan"`` / ``"gray"``.
#: ``None`` means "inherit the terminal default" (matches the convention
#: used throughout :data:`DEFAULT_MARKDOWN_THEME`). The mapping is
#: deliberately a separate dict (not inline in :data:`DEFAULT_MARKDOWN_THEME`)
#: so :func:`_resolve_theme_color` can resolve any semantic key via a
#: single lookup.
#:
#: These keys are *defined* in PR1 but not yet wired into the legacy
#: ``h1_color`` / ``code_color`` / ``quote_color`` defaults — PR3 will
#: flip the legacy keys to resolve through the semantic layer. Defining
#: them now lets downstream callers opt in early without waiting for PR3.
SEMANTIC_COLORS: dict[str, str | None] = {
    "text": None,
    "accent": "cyan",
    "secondary": "blue",
    "muted": "gray",
    "border": "gray",
    "success": "green",
    "error": "red",
    "warning": "yellow",
    "info": "blue",
}


def _resolve_theme_color(
    theme: dict[str, Any],
    semantic_key: str,
    legacy_key: str,
) -> str | None:
    """Resolve a colour from ``theme``, preferring the legacy key.

    PR1 introduces a semantic colour layer. Each semantic name (e.g.
    ``"accent"``) has a default concrete colour in
    :data:`SEMANTIC_COLORS` (e.g. ``"cyan"``) and a corresponding theme
    override key with a ``_color`` suffix (e.g. ``"accent_color"``).
    Legacy per-block colour keys (e.g. ``"h1_color"``) still exist for
    backwards compatibility.

    Resolution order:

    1. ``legacy_key`` (e.g. ``"h1_color"``) — if present in ``theme``
       and not ``None``, return its value verbatim (possibly resolving
       one level through :data:`SEMANTIC_COLORS` if the value is itself
       a semantic name like ``"accent"``). This preserves the existing
       behaviour for callers that pass ``theme={"h1_color": "cyan"}``.
    2. ``f"{semantic_key}_color"`` (e.g. ``"accent_color"``) — if
       present in ``theme``, honour the caller's semantic override
       (again resolving through :data:`SEMANTIC_COLORS` if the value is
       a semantic name).
    3. :data:`SEMANTIC_COLORS` default for ``semantic_key``.

    A value of ``None`` means "inherit the terminal default" (matches
    the convention used throughout :data:`DEFAULT_MARKDOWN_THEME`).
    The function never raises; an unknown ``semantic_key`` yields
    ``None``.
    """
    legacy = theme.get(legacy_key)
    if legacy is not None:
        # A legacy value may itself be a semantic name (``"accent"``) —
        # resolve one level deeper so ``theme={"h1_color": "accent"}``
        # works once PR3 flips the defaults.
        if isinstance(legacy, str) and legacy in SEMANTIC_COLORS:
            return SEMANTIC_COLORS[legacy]
        return legacy if isinstance(legacy, str) else None

    semantic_theme_key = f"{semantic_key}_color"
    if semantic_theme_key in theme:
        override = theme.get(semantic_theme_key)
        if isinstance(override, str) and override in SEMANTIC_COLORS:
            return SEMANTIC_COLORS[override]
        if isinstance(override, str) or override is None:
            return override

    return SEMANTIC_COLORS.get(semantic_key)


#: Default theme: per-block colour / weight hints. PR3 rewrites the
#: defaults to claude-code style: headings use the terminal's default
#: text colour (not the pre-PR3 rainbow palette), with h1 distinguished
#: by italic + underline in addition to bold; inline code / links use
#: semantic colour keys (``accent`` / ``muted``) so callers can re-skin
#: the whole document via the semantic layer; blockquotes render with a
#: visible left bar (``▎``) by default.
#:
#: ``None`` means "inherit the terminal default". Colour names use
#: PyInk's :data:`ink.render.ansi.NAMED_COLORS` vocabulary (``"gray"``
#: instead of ``"brightBlack"`` — both spell SGR 90, but only
#: ``"gray"`` / ``"grey"`` / ``"blackBright"`` are in the table). A
#: legacy colour key whose value is itself a semantic name (e.g.
#: ``"accent"``) resolves through :data:`SEMANTIC_COLORS` via
#: :func:`_resolve_theme_color`.
#:
#: ``h{n}_bold`` entries carry ``bool`` values; colour entries carry
#: ``str | None``. The dict is typed as ``dict[str, Any]`` so callers
#: can override any entry without hitting a union-narrowing error.
DEFAULT_MARKDOWN_THEME: dict[str, Any] = {
    # Headings — claude-code style: terminal default colour (None) +
    # bold at every level; h1 adds italic + underline for emphasis (see
    # the ``h{n}_underline`` / ``h{n}_italic`` knobs below).
    "h1_color": None,
    "h1_bold": True,
    "h2_color": None,
    "h2_bold": True,
    "h3_color": None,
    "h3_bold": True,
    "h4_color": None,
    "h4_bold": True,
    "h5_color": None,
    "h5_bold": True,
    "h6_color": None,
    "h6_bold": True,
    # Inline — semantic colour keys. ``code_color="accent"`` resolves to
    # cyan (SEMANTIC_COLORS["accent"]); ``link_color="accent"`` matches
    # so code + links read as the same semantic accent. ``quote_color``
    # uses ``muted`` so blockquote inline text reads as dim without a
    # hard-coded ``gray``.
    "code_color": "accent",
    "code_bg": None,
    "link_color": "accent",
    "quote_color": "muted",
    "code_block_lang_color": "gray",
    "hr_color": None,
    # ---- PR1: Semantic colour keys --------------------------------------
    # Defined now so downstream callers can opt in via
    # ``theme={"h1_color": "accent"}`` (resolved through
    # :data:`SEMANTIC_COLORS`). PR3 will rewire the legacy colour keys
    # above to default-resolve through these semantic keys. ``None``
    # means "inherit the terminal default".
    "text_color": None,
    "accent_color": "cyan",
    "secondary_color": "blue",
    "muted_color": "gray",
    "border_color": "gray",
    "success_color": "green",
    "error_color": "red",
    "warning_color": "yellow",
    "info_color": "blue",
    # ---- PR1: Heading style knobs --------------------------------------
    # PR3 flips h1's italic + underline defaults to ``True`` so the
    # default h1 reads as claude-code (bold + italic + underline, no
    # rainbow colour). h2-h6 keep the pre-PR3 ``False`` defaults — bold
    # alone is enough differentiation once colour is removed.
    "h1_underline": True,
    "h1_italic": True,
    "h2_underline": False,
    "h2_italic": False,
    "h3_underline": False,
    "h3_italic": False,
    "h4_underline": False,
    "h4_italic": False,
    "h5_underline": False,
    "h5_italic": False,
    "h6_underline": False,
    "h6_italic": False,
    # ---- PR1: Blockquote bar -------------------------------------------
    # PR3 default: ``quote_bar_char="▎"`` (U+258E) draws a left bar in
    # the ``muted`` semantic colour, matching claude-code's blockquote
    # treatment. Setting ``quote_bar_char=None`` restores the pre-PR3
    # pure-indent (paddingLeft=2) behaviour.
    "quote_bar_char": "▎",
    "quote_bar_color": "muted",
    # ---- PR1: List nested markers --------------------------------------
    # ``list_ordered_nested_style="decimal"`` keeps the existing
    # ``1. 2. 3.`` at every depth. Other values: ``"alpha"`` (a. b. c.),
    # ``"roman"`` (i. ii. iii.), ``"auto"`` (decimal → alpha → roman by
    # depth).
    "list_ordered_nested_style": "decimal",
    # ``list_bullet_nested_chars="-"`` keeps the existing single-dash
    # bullet at every depth. A multi-char string (e.g. ``"-*+"``) cycles
    # by ``depth % len(chars)``.
    "list_bullet_nested_chars": "-",
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
    # ---- PR2: Table rendering knobs ------------------------------------
    # ``table_border_style`` selects the box-drawing character set used
    # for the table frame + the header/body separator. ``"single"`` (the
    # default) draws ``┌─┬─┐``; ``"rounded"`` uses ``╭─╮╯╰``; ``"double"``
    # uses ``╔═╗╝╚``; ``"none"`` disables the frame entirely (matching
    # the pre-PR2 borderless look).
    "table_border_style": "single",
    # ``table_border_color=None`` falls back to the theme's ``border``
    # semantic colour (resolved via :func:`_resolve_theme_color`); a
    # concrete colour name overrides it.
    "table_border_color": None,
    # Whether the header row renders bold. Mirrors the
    # ``table_header_bold`` convention from claude-code.
    "table_header_bold": True,
    # ``table_header_align=None`` lets each column's alignment (from the
    # ``:---:`` markers) apply to the header. A concrete value
    # (``"left"`` / ``"center"`` / ``"right"``) overrides per-column
    # alignment for the header row only.
    "table_header_align": None,
    # Cell padding (left + right) inside each column. Default 1 matches
    # claude-code's ``" " + cell + " "`` layout.
    "table_cell_padding": 1,
    # Separator between key and value when the table degrades to the
    # vertical / key-value layout (extreme terminal narrowness).
    "table_kv_separator": ":",
    # Degrade threshold: a single cell that wraps to more than this many
    # lines triggers the vertical / key-value fallback.
    "table_max_row_lines": 4,
    # Minimum column width — columns never shrink below this even in the
    # proportional-shrink branch (prevents degenerate 1-char columns).
    "table_min_column_width": 3,
    # Safety margin left between the table and the terminal right edge
    # to absorb resize-race measurement drift (claude-code: 4).
    "table_safety_margin": 4,
    # ---- PR3: Block spacing ---------------------------------------------
    # Per-block ``spacing_before_*`` / ``spacing_after_*`` knobs control
    # the blank-row gap between adjacent top-level blocks. The actual
    # gap inserted between two blocks is ``max(spacing_after_<prev>,
    # spacing_before_<next>)`` so a block that wants a bigger leading
    # gap (e.g. a heading) always wins over a preceding block's smaller
    # trailing gap. Defaults mirror claude-code's spacing rules:
    # headings get a leading blank row + 2 trailing blanks; paragraphs
    # get 1 trailing blank; other blocks get 1 leading + 1 trailing so
    # they visually separate from neighbours.
    "spacing_before_heading": 1,
    "spacing_after_heading": 2,
    "spacing_before_paragraph": 0,
    "spacing_after_paragraph": 1,
    "spacing_before_code_block": 1,
    "spacing_after_code_block": 1,
    "spacing_before_blockquote": 1,
    "spacing_after_blockquote": 1,
    "spacing_before_list": 0,
    "spacing_after_list": 1,
    "spacing_before_table": 1,
    "spacing_after_table": 1,
    "spacing_before_hr": 1,
    "spacing_after_hr": 1,
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
                "Install: pip install ink[markdown]"
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
    :data:`ink.externals.highlighted_code.DEFAULT_THEME`).
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

    The theme's ``__quote_color__`` flag (set by :func:`_render_blockquote`)
    holds the resolved quote colour or ``None``. When set, every inline
    text run picks up ``color=<quote_color>`` so the whole quote reads
    as muted text without us having to thread a parameter through every
    recursion level. ``None`` disables quote colouring (terminal default
    text colour).

    Inline leaves handled here:

    * ``text`` — verbatim text run with the active style.
    * ``code_inline`` — single run with the ``code_color`` from the
      theme. PR1: inline code inherits the surrounding bold / italic /
      strikethrough so ``**bold `code`**`` renders the code run bold
      too (previously the outer inline state was dropped).
    * ``softbreak`` / ``hardbreak`` — newline character; the parent
      ``Text`` treats ``\\n`` as a line break inside the same leaf.
    * ``link_open`` / ``link_close`` — wraps the contained text in an
      OSC 8 sequence (via :func:`_wrap_osc8`), with ``link_color``
      applied to the visible text. Nested styling inside a link is
      honoured (e.g. ``**[bold link](url)**`` works).
    """
    out: list[str] = []
    # Legacy-cleanup: the ``__quote_color__`` flag (set by
    # :func:`_render_blockquote`) carries the resolved quote colour or
    # ``None``. Pre-cleanup this was a ``__quote__`` boolean flag that
    # drove ``dimColor=True`` (SGR 2); now we apply the colour directly
    # so the default ``quote_color="muted"`` resolves to gray (SGR 90).
    # ``None`` (either outside a quote, or ``theme={"quote_color": None}``)
    # means no colour SGR is emitted.
    quote_color = theme.get("__quote_color__")

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
                )
                out.append(_wrap_osc8(styled, link_url))
            else:
                out.append(
                    apply_style(
                        text,
                        color=quote_color,
                        bold=bold,
                        italic=italic,
                        strikethrough=strikethrough,
                    )
                )
            i += 1
            continue

        if ctype == "code_inline":
            # PR3: ``code_color`` resolves through the semantic layer so
            # the default ``"accent"`` maps to cyan (SGR 36). A concrete
            # colour name (``"red"``) or ``None`` (terminal default) is
            # returned verbatim.
            color = _resolve_theme_color(theme, "accent", "code_color")
            # PR1: inline code now inherits the surrounding bold / italic /
            # strikethrough so ``**bold `code`**`` renders the code segment
            # bold too. Previously code_inline dropped the outer inline
            # state, which read as a typographic mismatch inside emphasised
            # spans. Inline code keeps its own ``code_color``; the quote
            # colour does NOT apply inside the code run (code has its own
            # colour semantic).
            out.append(
                apply_style(
                    child.content,
                    color=color,
                    bold=bold,
                    italic=italic,
                    strikethrough=strikethrough,
                )
            )
            i += 1
            continue

        if ctype in ("softbreak", "hardbreak"):
            out.append("\n")
            i += 1
            continue

        if ctype == "link_open":
            url = _link_url(child)
            inner, j = _collect_balanced(children, i, "link_open", "link_close")
            # PR3: ``link_color`` resolves through the semantic layer so
            # the default ``"accent"`` maps to cyan (SGR 36).
            inner_str = _render_inline(
                inner,
                theme,
                bold=bold,
                italic=italic,
                strikethrough=strikethrough,
                link_url=url,
                link_color=_resolve_theme_color(theme, "accent", "link_color"),
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

    PR1 adds ``h{n}_underline`` / ``h{n}_italic`` theme keys (default
    ``False``) so callers can opt into underline / italic per heading
    level without touching the default rainbow-colour + bold look.
    """
    level = token.tag  # "h1" / "h2" / … / "h6"
    suffix = level[1:]
    # PR3: heading colour resolves through the semantic layer so
    # ``h1_color="accent"`` (or the semantic default) maps to a concrete
    # SGR colour name. ``None`` stays ``None`` (inherit terminal default).
    color = _resolve_theme_color(theme, "text", f"h{suffix}_color")
    bold = _theme_bool(theme.get(f"h{suffix}_bold", True))
    underline = _theme_bool(theme.get(f"h{suffix}_underline", False))
    italic = _theme_bool(theme.get(f"h{suffix}_italic", False))

    # Render the inline content first, then wrap the whole heading in
    # the heading colour / bold so headings read uniformly. We compose
    # by applying the heading's style to the already-styled inline
    # segments — apply_style on the joined string would double-wrap
    # any segment that already had bold, which is fine because SGR is
    # idempotent in practice.
    inline_str = _render_inline_token(inline, theme)
    styled = apply_style(
        inline_str,
        color=color,
        bold=bold,
        underline=underline,
        italic=italic,
    )
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
        from ink.externals.highlighted_code import HighlightedCode
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
    *,
    columns: int | None = None,
) -> tuple[Element, int]:
    """Render a ``blockquote_open`` ... ``blockquote_close`` span.

    Returns the rendered ``Box`` and the index just past the matching
    ``blockquote_close``. The contents are rendered as nested blocks
    (typically paragraphs), then wrapped in a single ``Box``.

    Two shapes are supported:

    * **Bar mode (PR3 default)** — ``quote_bar_char`` is a non-empty
      string (default ``"▎"``): the blockquote becomes a row ``Box``
      with a coloured left bar (``Text(quote_bar_char,
      color=quote_bar_color)``) and a 1-space gutter replacing the
      ``paddingLeft=2`` indent. ``quote_bar_color`` resolves through
      the semantic layer so the default ``"muted"`` maps to gray (SGR
      90); ``None`` falls back to ``dimColor`` for a muted look without
      a hard-coded colour.
    * **Pure-indent mode** — ``quote_bar_char`` is ``None``: the
      blockquote is wrapped in a column ``Box`` with ``paddingLeft=2``.
      The ``quote_color`` from the theme is applied to inline runs via
      the ``__quote_color__`` flag (resolved here to a concrete colour
      name or ``None``). A non-None value paints every inline text run
      with that colour; ``None`` leaves the terminal default text
      colour.

    PR3: ``columns`` is threaded into the recursive :func:`_render_tokens`
    call so a table nested inside a blockquote responsively shrinks to
    the blockquote's available width (bar + gutter ≈ 2 cells).
    """
    inner_tokens, j = _collect_balanced(tokens, start, "blockquote_open", "blockquote_close")
    quote_theme = dict(theme)
    # PR3 legacy-cleanup: replace the ``__quote__`` boolean flag (which only
    # drove ``dimColor=True``) with ``__quote_color__`` carrying the resolved
    # colour. A non-None value means "inside a quote, paint inline text with
    # this colour"; ``None`` disables quote colouring entirely (default text
    # colour). The default ``quote_color="muted"`` resolves to gray (SGR 90).
    quote_theme["__quote_color__"] = _resolve_theme_color(theme, "muted", "quote_color")

    bar_char = theme.get("quote_bar_char")
    # Both modes consume 2 cells of indent: bar mode uses bar (1) +
    # gutter (1); pure-indent mode uses paddingLeft=2. The constant lets
    # the recursive ``_render_tokens`` call budget the inner content
    # width so a nested table can shrink to fit the blockquote.
    bar_indent = 2
    inner_columns: int | None = None
    if columns is not None and columns > bar_indent:
        inner_columns = columns - bar_indent
    inner_elements, _ = _render_tokens(
        inner_tokens, 0, quote_theme, columns=inner_columns
    )

    if bar_char:
        bar_color = _resolve_theme_color(theme, "muted", "quote_bar_color")
        bar_props: dict[str, Any] = {"dimColor": True}
        if bar_color is not None:
            bar_props = {"color": bar_color}
        return (
            Box(
                Text(bar_char, **bar_props),
                Box(*inner_elements, flexDirection="column", paddingLeft=1),
                flexDirection="row",
            ),
            j + 1,
        )

    return (
        Box(*inner_elements, flexDirection="column", paddingLeft=2),
        j + 1,
    )


def _list_marker(
    is_ordered: bool,
    counter: int,
    depth: int,
    theme: dict[str, Any],
) -> str:
    """Compute the marker string for a list item at the given ``depth``.

    * Bullet lists: read ``list_bullet_nested_chars`` (default ``"-"``).
      A multi-char string cycles by ``depth % len(chars)`` so ``"-*+"``
      yields ``-`` / ``*`` / ``+`` / ``-`` / … across nesting levels.
      A single-char string (the default) yields the same char at every
      depth (matches the pre-PR1 behaviour).
    * Ordered lists: read ``list_ordered_nested_style`` (default
      ``"decimal"``). ``"alpha"`` renders ``a.`` / ``b.`` / … (cycles
      every 26), ``"roman"`` renders lower-case Roman numerals
      (``i.`` / ``ii.`` / …), ``"auto"`` switches by depth:
      depth 0 → decimal, depth 1 → alpha, depth ≥ 2 → roman.

    The returned marker does NOT include the trailing space — callers
    append it so they control the gutter width.
    """
    if not is_ordered:
        chars = theme.get("list_bullet_nested_chars") or "-"
        if not isinstance(chars, str) or not chars:
            chars = "-"
        char = chars[depth % len(chars)]
        return char

    style = theme.get("list_ordered_nested_style") or "decimal"
    if not isinstance(style, str):
        style = "decimal"
    style = style.strip().lower()

    if style == "auto":
        if depth <= 0:
            style = "decimal"
        elif depth == 1:
            style = "alpha"
        else:
            style = "roman"

    if style == "alpha":
        # 1 → a, 26 → z, 27 → a (cycle, mirroring list behaviour in
        # most renderers; ordered lists rarely exceed 26 items).
        letter = chr(ord("a") + (counter - 1) % 26)
        return f"{letter}."
    if style == "roman":
        return f"{_to_roman(counter)}."

    # Default / fallback: decimal.
    return f"{counter}."


def _to_roman(n: int) -> str:
    """Convert a positive int to lower-case Roman numerals.

    Used by :func:`_list_marker` for the ``roman`` / ``auto`` nested
    styles. Supports 1-3999 (well beyond any realistic list length);
    values outside that range fall back to the decimal string so we
    never crash on pathological input.
    """
    if n <= 0 or n >= 4000:
        return str(n)
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    sym = ["m", "cm", "d", "cd", "c", "xc", "l", "xl", "x", "ix", "v", "iv", "i"]
    out: list[str] = []
    for v, s in zip(val, sym, strict=True):
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


def _render_list(
    tokens: list[Token],
    start: int,
    theme: dict[str, Any],
    *,
    depth: int = 0,
    columns: int | None = None,
) -> tuple[Element, int]:
    """Render a ``bullet_list_open`` / ``ordered_list_open`` span.

    Each ``list_item_open`` ... ``list_item_close`` becomes one column
    row. The marker is computed by :func:`_list_marker` based on
    ``depth`` and the ``list_ordered_nested_style`` /
    ``list_bullet_nested_chars`` theme keys. The item's first block
    (typically a paragraph) renders inline with the marker; any
    subsequent blocks (nested lists, code blocks, …) render below,
    indented by ``paddingLeft``.

    ``depth`` is threaded into nested lists by passing ``depth + 1``
    to :func:`_render_list_item`'s recursive :func:`_render_tokens`
    call, so a list nested two levels deep picks up the right marker
    style automatically.

    PR3: ``columns`` is threaded into the recursive
    :func:`_render_list_item` call so a table nested inside a list
    item responsively shrinks to the item's available width (the
    marker + the nested paddingLeft=2 together consume ≈ 4 cells).
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
            rows.append(
                _render_list_item(
                    item_tokens, is_ordered, counter, theme,
                    depth=depth, columns=columns,
                )
            )
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
    *,
    depth: int = 0,
    columns: int | None = None,
) -> Element:
    """Render a single list item.

    The first block in the item is rendered inline with the marker
    (so ``- a`` reads as one line). Subsequent blocks are wrapped in a
    nested column ``Box`` with ``paddingLeft=2`` so nested lists and
    multi-paragraph items indent under the marker.

    ``depth`` is passed through to :func:`_render_tokens` so any nested
    ``bullet_list_open`` / ``ordered_list_open`` inside ``rest_tokens``
    recurses into :func:`_render_list` with ``depth + 1``, picking up
    the next marker style (decimal → alpha → roman for ordered,
    cycling chars for bullet).

    PR3: ``columns`` is threaded into the recursive :func:`_render_tokens`
    call so a table nested inside a list item responsively shrinks. The
    nested ``paddingLeft=2`` consumes 2 cells; the marker gutter consumes
    ≈ 2 more (``"- "`` / ``"1. "``), so the inner content gets
    ``columns - 4`` to render against.
    """
    marker = _list_marker(is_ordered, counter, depth, theme)
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
    # Nested lists inside this item start at depth + 1 so their markers
    # shift (decimal → alpha → roman, or cycling bullet chars). Thread
    # ``columns`` through so nested tables shrink to the item's width.
    # The marker gutter (≈ 2 cells) + nested paddingLeft (2 cells) are
    # subtracted so the inner content gets an honest width budget.
    inner_columns: int | None = None
    if columns is not None and columns > 4:
        inner_columns = columns - 4
    rest_elements, _ = _render_tokens(
        rest_tokens, 0, theme, depth=depth + 1, columns=inner_columns
    )

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


#: Table-specific cross characters keyed by the same names used in
#: :data:`ink.render.ansi.BORDER_STYLES` (``single`` / ``double`` /
#: ``round`` / ``bold``). Each entry carries the 5 cross glyphs a
#: bordered table needs on top of the outer-corner set already provided
#: by ``BORDER_STYLES``: ``top_cross`` (T-down), ``bottom_cross``
#: (T-up), ``mid_left`` (T-right), ``mid_right`` (T-left), ``mid_cross``
#: (4-way). The horizontal fill and vertical edge come from
#: ``BORDER_STYLES`` via :func:`_get_table_border_chars`.
#:
#: Rationale (``research/table-border-options.md:130``): we deliberately
#: do NOT extend ``BORDER_STYLES`` itself with these cross keys — the
#: layout renderer's ``_paint_box_border`` would not read them and the
#: rework cost is out of scope. Cross characters stay a table-only
#: concern and live here.
_TABLE_CROSS_CHARS: dict[str, dict[str, str]] = {
    "single": {
        "top_cross": "┬", "mid_cross": "┼", "mid_left": "├",
        "mid_right": "┤", "bottom_cross": "┴",
    },
    "double": {
        "top_cross": "╦", "mid_cross": "╬", "mid_left": "╠",
        "mid_right": "╣", "bottom_cross": "╩",
    },
    "round": {
        "top_cross": "┬", "mid_cross": "┼", "mid_left": "├",
        "mid_right": "┤", "bottom_cross": "┴",
    },
    "bold": {
        "top_cross": "┳", "mid_cross": "╋", "mid_left": "┣",
        "mid_right": "┫", "bottom_cross": "┻",
    },
}

#: Alias map from the markdown-facing style name to the
#: :data:`ink.render.ansi.BORDER_STYLES` key. ``"rounded"`` is the
#: historical markdown name (PR2 default); ``BORDER_STYLES`` calls the
#: same glyph set ``"round"``. The alias keeps both names accepted
#: without duplicating the glyph dict. ``BORDER_STYLES["rounded"]`` is
#: also defined as an alias entry so the two names are
#: interchangeable on the ansi side too.
_TABLE_BORDER_ALIASES: dict[str, str] = {"rounded": "round"}


def _get_table_border_chars(style: str) -> dict[str, str]:
    """Build the table frame glyph set for ``style``.

    The outer corners / edges come from
    :data:`ink.render.ansi.BORDER_STYLES` (single source of truth for
    box-drawing outer-corner glyphs); the cross pieces come from
    :data:`_TABLE_CROSS_CHARS`. The returned dict uses the
    snake_case keys the table renderer consumes internally
    (``top_left`` / ``top_right`` / ``bottom_left`` / ``bottom_right``
    / ``horizontal`` / ``vertical`` + the 5 cross keys).

    Unknown styles fall back to ``"single"`` so a typo in the theme
    never crashes the renderer — the table still renders, just with the
    default frame. Callers handling ``"none"`` short-circuit before
    reaching this function.
    """
    ansi_style = _TABLE_BORDER_ALIASES.get(style, style)
    base = BORDER_STYLES.get(ansi_style, BORDER_STYLES["single"])
    cross = _TABLE_CROSS_CHARS.get(ansi_style, _TABLE_CROSS_CHARS["single"])
    return {
        "top_left": base["topLeft"],
        "top_right": base["topRight"],
        "bottom_left": base["bottomLeft"],
        "bottom_right": base["bottomRight"],
        "horizontal": base["top"],
        "vertical": base["left"],
        **cross,
    }


def _parse_table_align(token: Token) -> str:
    """Extract the column alignment from a ``th_open`` / ``td_open`` token.

    markdown-it-py stores the alignment in ``token.attrs['style']`` as
    ``"text-align:left|center|right"`` (verified experimentally — see
    ``research/RESEARCH-NOTES.md``). A column with no alignment marker
    has an empty ``attrs`` and defaults to ``"left"``.

    Per the CommonMark table spec, alignment is a column-level property
    derived from the ``:---:`` separator row; body cells inherit it.
    We therefore only consult the header ``th_open`` tokens when
    building the per-column align list (see :func:`_render_table`).
    """
    style = (token.attrs or {}).get("style", "")
    if not isinstance(style, str):
        return "left"
    if "text-align:center" in style:
        return "center"
    if "text-align:right" in style:
        return "right"
    return "left"


def _pad_aligned(text: str, text_w: int, width: int, align: str) -> str:
    """Pad ``text`` to ``width`` cells according to ``align``.

    Mirrors claude-code's ``padAligned``: left-align pads on the right,
    right-align pads on the left, center splits the padding with the
    extra cell on the right (matches the common ``str.center`` convention).
    ``text_w`` is the caller-measured display width (via
    :func:`string_width`) so ANSI-styled cells pad correctly.
    """
    if text_w >= width:
        return text
    pad = width - text_w
    if align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + text + " " * right
    if align == "right":
        return " " * pad + text
    return text + " " * pad


def _table_border_chars(style: str) -> dict[str, str] | None:
    """Return the box-drawing glyph set for ``style``.

    Returns ``None`` for ``"none"`` (no frame). Unknown styles fall
    back to ``"single"`` so a typo in the theme never crashes the
    renderer — the table still renders, just with the default frame.
    The outer-corner glyphs are sourced from
    :data:`ink.render.ansi.BORDER_STYLES`; only the cross pieces are
    table-specific (see :data:`_TABLE_CROSS_CHARS`).
    """
    if style == "none":
        return None
    return _get_table_border_chars(style)


def _hline(
    chars: dict[str, str],
    widths: list[int],
    padding: int,
    left: str,
    cross: str,
    right: str,
) -> str:
    """Build a horizontal border line for the table frame.

    ``widths`` are the per-column content widths; each column is drawn
    as ``horizontal * (width + 2 * padding)`` so the border spans the
    full padded cell. Columns are joined with ``cross`` and capped with
    ``left`` / ``right``.
    """
    fill = chars["horizontal"]
    parts = [left]
    n = len(widths)
    for i, w in enumerate(widths):
        parts.append(fill * (w + 2 * padding))
        parts.append(cross if i < n - 1 else right)
    return "".join(parts)


def _dataline(
    chars: dict[str, str],
    cells: list[str],
    widths: list[int],
    aligns: list[str],
    padding: int,
    border_color: str | None = None,
) -> str:
    """Build a data row line: ``│ cell │ cell │``.

    Each cell is padded to its column width according to the column's
    alignment, then wrapped in ``padding``-wide gutters on both sides.
    Cells are joined with the vertical separator and capped with the
    left / right edge glyphs.

    ``border_color`` applies the same colour to the vertical separators
    that :func:`_hline` applies to the horizontal borders, so the whole
    frame reads as one colour. ``None`` leaves the separators at the
    terminal default (used when the caller has disabled border colouring).
    Cell content is never touched — each separator is independently
    wrapped in its own SGR run so the reset does not bleed into the
    cell's inline styling.
    """
    vertical = chars["vertical"]
    v_segment = (
        apply_style(vertical, color=border_color)
        if border_color is not None
        else vertical
    )
    parts = [v_segment]
    n = len(widths)
    for i in range(n):
        cell = cells[i] if i < len(cells) else ""
        cell_w = string_width(cell)
        padded = _pad_aligned(cell, cell_w, widths[i], aligns[i])
        parts.append(" " * padding + padded + " " * padding)
        parts.append(v_segment)
    return "".join(parts)


def _render_table(
    tokens: list[Token],
    start: int,
    theme: dict[str, Any],
    *,
    columns: int | None = None,
) -> tuple[Element, int]:
    """Render a ``table_open`` ... ``table_close`` span as a bordered table.

    CommonMark tables arrive as ``thead_open`` (one row of ``th``) +
    ``tbody_open`` (rows of ``td``). For each cell we collect the
    ``inline`` child token and render it via :func:`_render_inline_token`
    so inline styling (``**bold**`` / ```code```) is honoured inside
    cells. Column alignment is read from the header row's ``th_open``
    ``attrs['style']`` (see :func:`_parse_table_align`).

    Layout passes through three regimes (ported from claude-code's
    ``MarkdownTable.tsx``):

    * **A — ideal**: ``sum(ideal_widths)`` fits the available width →
      use each column's natural max width.
    * **B — proportional shrink**: ideal overflows but min widths fit →
      distribute the slack by each column's ``ideal - min`` ratio.
    * **C — key-value fallback**: even min widths overflow → degrade
      to a vertical ``header: value`` layout per row.

    The available width comes from ``columns`` (passed by
    :func:`_render_tokens`, which got it from
    :func:`_render_markdown_to_string`, which in turn read it from the
    layout's text-width context — see ``research/layout-width-context.md``).
    When ``columns`` is ``None`` (defensive — shouldn't happen post-PR2
    since both static and reactive paths route through
    :func:`_render_markdown_to_string`), we fall back to the ideal
    widths without responsive shrink.

    The frame is drawn with box-drawing characters (``single`` /
    ``rounded`` / ``double`` / ``none``) via :func:`_hline` and
    :func:`_dataline`; each border line and data row is a ``Text`` leaf
    inside a column ``Box`` (per ``research/table-border-options.md`` —
    the layout engine's ``Box(borderStyle=...)`` cannot draw the
    internal ``┬┴┼`` crosses).
    """
    body_tokens, end = _collect_balanced(tokens, start, "table_open", "table_close")

    # Walk the table tokens once, collecting:
    # * ``header``: the list of header cell strings (rendered inline).
    # * ``aligns``: per-column alignment from the header's th_open attrs.
    # * ``rows``: list of body rows, each a list of cell strings.
    # * ``raw_cells``: the (token, is_header) pairs per cell so we can
    #   re-render with proper styling when computing wrapped line counts.
    header: list[str] = []
    aligns: list[str] = []
    rows: list[list[str]] = []
    current_row: list[str] = []
    in_thead = False
    i = 0
    while i < len(body_tokens):
        t = body_tokens[i]
        if t.type == "thead_open":
            in_thead = True
        elif t.type == "thead_close":
            in_thead = False
        elif t.type == "tr_open":
            current_row = []
        elif t.type == "tr_close":
            if in_thead:
                header = current_row
            else:
                rows.append(current_row)
        elif t.type in ("th_open", "td_open"):
            # Read alignment only from the header row's th_open — the
            # CommonMark spec says alignment is column-level, derived
            # from the ``:---:`` separator, so body cells inherit it.
            if in_thead and t.type == "th_open":
                aligns.append(_parse_table_align(t))
            k = i + 1
            cell_text = ""
            while k < len(body_tokens) and body_tokens[k].type not in (
                "th_close",
                "td_close",
            ):
                if body_tokens[k].type == "inline":
                    # Render the cell with full inline styling so
                    # ``**bold**`` / `` `code` `` carry SGR sequences.
                    # ``string_width`` strips CSI when we measure widths.
                    cell_text = _render_inline_token(body_tokens[k], theme)
                k += 1
            current_row.append(cell_text)
            i = k
        i += 1

    if not rows and not header:
        return Box(flexDirection="column"), end + 1

    all_rows = ([header] if header else []) + rows
    n_cols = max(len(r) for r in all_rows) if all_rows else 0
    if n_cols == 0:
        return Box(flexDirection="column"), end + 1

    # Normalise aligns to n_cols (default left for unspecified columns).
    while len(aligns) < n_cols:
        aligns.append("left")
    aligns = aligns[:n_cols]

    # Theme knobs.
    border_style = str(theme.get("table_border_style", "single"))
    chars = _table_border_chars(border_style)
    padding = int(theme.get("table_cell_padding", 1))
    min_col_w = int(theme.get("table_min_column_width", 3))
    max_row_lines = int(theme.get("table_max_row_lines", 4))
    safety_margin = int(theme.get("table_safety_margin", 4))
    header_bold = _theme_bool(theme.get("table_header_bold", True))
    header_align_override = theme.get("table_header_align")
    kv_separator = str(theme.get("table_kv_separator", ":"))

    # Apply bold to the header cells (post-inline-render) so the SGR
    # sequence wraps the already-styled content. ``apply_style`` is
    # idempotent in practice for nested bold.
    styled_header = [
        apply_style(cell, bold=header_bold) if header_bold else cell
        for cell in header
    ]

    # Compute per-column min / ideal widths across all rows (header + body).
    # ``min_w`` is the longest single *word* in any cell of the column
    # (prevents degenerate word-wrap splits); ``ideal_w`` is the longest
    # full cell. Both floor at ``min_col_w``.
    def _cell_plain_widths(cells: list[str]) -> tuple[int, int]:
        """Return (min_word_width, full_cell_width) for a list of cells."""
        if not cells:
            return min_col_w, min_col_w
        # Strip ANSI for word-splitting so SGR bytes don't masquerade as
        # word characters. The returned width is a display width.
        from ink.layout.measure import _strip_ansi
        plain = _strip_ansi(" ".join(cells))
        words = [w for w in plain.split() if w]
        if not words:
            return min_col_w, max(min_col_w, string_width(plain))
        longest_word = max(string_width(w) for w in words)
        full = max(string_width(_strip_ansi(c)) for c in cells)
        return max(min_col_w, longest_word), max(min_col_w, full)

    min_widths: list[int] = []
    ideal_widths: list[int] = []
    for idx in range(n_cols):
        col_cells = []
        if header:
            col_cells.append(styled_header[idx] if idx < len(styled_header) else "")
        for r in rows:
            col_cells.append(r[idx] if idx < len(r) else "")
        min_w, ideal_w = _cell_plain_widths(col_cells)
        min_widths.append(min_w)
        ideal_widths.append(ideal_w)

    # Determine the available content width.
    if columns is not None and columns > 0:
        # borderOverhead = 1 (left edge) + n_cols * (2*padding + 1 vertical)
        # matches claude-code's ``1 + numCols * 3`` for padding=1.
        border_overhead = 1 + n_cols * (2 * padding + 1)
        available = max(columns - border_overhead - safety_margin, n_cols * min_col_w)
    else:
        available = None

    # Choose column widths via the three-regime algorithm.
    total_min = sum(min_widths)
    total_ideal = sum(ideal_widths)

    needs_hard_wrap = False
    if available is None or total_ideal <= available:
        # Regime A — ideal widths.
        col_widths = list(ideal_widths)
    elif total_min <= available:
        # Regime B — proportional shrink: each column keeps its min,
        # the slack is distributed by the (ideal - min) ratio.
        extra_space = available - total_min
        overflows = [ideal_widths[i] - min_widths[i] for i in range(n_cols)]
        total_overflow = sum(overflows)
        col_widths = []
        for i in range(n_cols):
            if total_overflow == 0:
                col_widths.append(min_widths[i])
            else:
                extra = int((overflows[i] / total_overflow) * extra_space)
                col_widths.append(min_widths[i] + extra)
    else:
        # Regime C — too narrow even for min widths. Scale by min ratio
        # and allow hard-wrap; the safety net below may still degrade
        # to key-value if wrapping blows past ``max_row_lines``.
        needs_hard_wrap = True
        scale = available / total_min if total_min > 0 else 1.0
        col_widths = [
            max(int(w * scale), min_col_w) for w in min_widths
        ]

    # Decide whether to degrade to key-value. We wrap each cell to its
    # column width and count the max number of wrapped lines across all
    # cells; if any cell exceeds ``max_row_lines``, fall back.
    def _max_wrap_lines() -> int:
        max_lines = 1
        wrap_mode: WrapMode = "hard" if needs_hard_wrap else "wrap"
        for idx in range(n_cols):
            if header:
                cell = styled_header[idx] if idx < len(styled_header) else ""
                wrapped = wrap_text(cell, col_widths[idx], mode=wrap_mode)
                max_lines = max(max_lines, len(wrapped))
            for r in rows:
                cell = r[idx] if idx < len(r) else ""
                wrapped = wrap_text(cell, col_widths[idx], mode=wrap_mode)
                max_lines = max(max_lines, len(wrapped))
        return max_lines

    use_kv = False
    if available is not None and _max_wrap_lines() > max_row_lines:
        use_kv = True
    # Safety net: if even the ideal layout can't fit, degrade.
    if available is not None and total_min > available:
        use_kv = True

    if use_kv:
        # Pass the *unstyled* header to the key-value renderer —
        # :func:`_render_table_kv` applies its own bold styling to the
        # ``label:`` segment, so pre-styled header cells would double-wrap
        # the SGR sequence (``\x1b[1m\x1b[1mName\x1b[0m:\x1b[0m``).
        return _render_table_kv(
            header, rows, aligns, columns, theme, kv_separator,
        ), end + 1

    # Build the bordered / borderless table.
    header_aligns = list(aligns)
    if isinstance(header_align_override, str):
        header_aligns = [header_align_override] * n_cols

    if chars is None:
        # ``table_border_style="none"`` — render borderless rows like
        # the pre-PR2 path (row Box of padded cell Text leaves).
        rendered_rows: list[Element] = []
        all_data_rows = ([styled_header] if header else []) + rows
        for r in all_data_rows:
            cells_el: list[Element] = []
            for idx in range(n_cols):
                text = r[idx] if idx < len(r) else ""
                cell_w = string_width(text)
                pad = col_widths[idx] - cell_w
                if pad < 0:
                    pad = 0
                gutter = " " * padding if idx < n_cols - 1 else ""
                cells_el.append(Text(text + " " * pad + gutter))
            rendered_rows.append(Box(*cells_el, flexDirection="row"))
        return Box(*rendered_rows, flexDirection="column"), end + 1

    # Bordered path: each border line and data row is a single Text leaf.
    border_color = theme.get("table_border_color")
    if border_color is None:
        border_color = _resolve_theme_color(theme, "border", "border_color")

    top_border = _hline(
        chars, col_widths, padding,
        chars["top_left"], chars["top_cross"], chars["top_right"],
    )
    mid_border = _hline(
        chars, col_widths, padding,
        chars["mid_left"], chars["mid_cross"], chars["mid_right"],
    )
    bottom_border = _hline(
        chars, col_widths, padding,
        chars["bottom_left"], chars["bottom_cross"], chars["bottom_right"],
    )

    if border_color is not None:
        top_border = apply_style(top_border, color=border_color)
        mid_border = apply_style(mid_border, color=border_color)
        bottom_border = apply_style(bottom_border, color=border_color)

    lines: list[Element] = []
    if header:
        lines.append(Text(top_border))
        header_line = _dataline(
            chars, styled_header, col_widths, header_aligns, padding, border_color,
        )
        lines.append(Text(header_line))
        lines.append(Text(mid_border))
    else:
        lines.append(Text(top_border))
    for r in rows:
        lines.append(Text(_dataline(chars, r, col_widths, aligns, padding, border_color)))
    lines.append(Text(bottom_border))

    return Box(*lines, flexDirection="column"), end + 1


def _render_table_kv(
    header: list[str],
    rows: list[list[str]],
    aligns: list[str],
    columns: int | None,
    theme: dict[str, Any],
    kv_separator: str,
) -> Element:
    """Render a table as a vertical key-value list (the narrow-terminal fallback).

    Each body row becomes a block of ``header: value`` lines (one per
    column), with a ``─`` separator between rows. Mirrors claude-code's
    ``renderVerticalFormat``: the label (header cell) is bolded, the
    separator sits between rows, and the value is wrapped to the
    available width minus the label + separator indentation.
    """
    n_cols = max(len(header), max((len(r) for r in rows), default=0))
    if n_cols == 0:
        return Box(flexDirection="column")

    available = columns if isinstance(columns, int) and columns > 0 else 80
    sep_width = min(max(available - 1, 1), 40)
    separator = "─" * sep_width
    sep_color = _resolve_theme_color(theme, "border", "border_color")
    if sep_color is not None:
        separator_styled = apply_style(separator, color=sep_color)
    else:
        separator_styled = separator

    lines: list[Element] = []
    for row_idx, r in enumerate(rows):
        if row_idx > 0:
            lines.append(Text(separator_styled))
        for col_idx in range(n_cols):
            label = header[col_idx] if col_idx < len(header) else f"Column {col_idx + 1}"
            value = r[col_idx] if col_idx < len(r) else ""
            # Strip any newlines from the value so the key-value line
            # reads as a single ``label: value`` row.
            from ink.layout.measure import _strip_ansi
            value_plain = _strip_ansi(value).replace("\n", " ").strip()
            label_w = string_width(_strip_ansi(label))
            first_line_w = max(available - label_w - 3, 10)
            wrapped = wrap_text(value_plain, first_line_w, mode="wrap") if value_plain else [""]
            label_styled = apply_style(label + kv_separator, bold=True)
            lines.append(Text(label_styled + " " + (wrapped[0] if wrapped else "")))
            for cont in wrapped[1:]:
                lines.append(Text("  " + cont))
    return Box(*lines, flexDirection="column")


# ---------------------------------------------------------------------------
# Block rendering: token walker
# ---------------------------------------------------------------------------


#: Map a block token type to the spacing-theme-key suffix used by
#: :func:`_render_tokens` to look up ``spacing_before_<suffix>`` /
#: ``spacing_after_<suffix>``. ``None`` means "no spacing rule" — the
#: block is rendered without contributing to the gap calculation (this
#: is the case for bare inline tokens and unrecognised structural
#: noise). Keeping the map module-level makes the walker cheap and lets
#: callers reason about which token types participate in spacing.
_BLOCK_TYPE_FOR_SPACING: dict[str, str] = {
    "heading_open": "heading",
    "paragraph_open": "paragraph",
    "fence": "code_block",
    "code_block": "code_block",
    "blockquote_open": "blockquote",
    "bullet_list_open": "list",
    "ordered_list_open": "list",
    "table_open": "table",
    "hr": "hr",
    "inline": "paragraph",  # bare inline treated as a paragraph
}


def _block_spacing(
    theme: dict[str, Any], after: str | None, before: str | None
) -> int:
    """Return the blank-row gap to insert between two adjacent blocks.

    The gap is ``max(spacing_after_<after>, spacing_before_<before>)``
    so a block that wants a bigger leading gap (e.g. a heading's
    ``spacing_before_heading=1``) always wins over a preceding block's
    smaller trailing gap. ``None`` for either side means "no
    contribution" (treated as 0). The result is clamped to be
    non-negative; theme values are coerced to ``int`` so a stray
    ``"1"`` from a config file still works.
    """
    after_val = 0
    if after is not None:
        after_val = int(theme.get(f"spacing_after_{after}", 0) or 0)
    before_val = 0
    if before is not None:
        before_val = int(theme.get(f"spacing_before_{before}", 0) or 0)
    return max(after_val, before_val)


def _spacer_rows(n: int) -> list[Element]:
    """Return ``n`` empty ``Text`` leaves to act as blank-row spacers.

    A blank ``Text("")`` renders as a single empty row; stacking ``n``
    of them inserts ``n`` blank rows between adjacent blocks. ``n<=0``
    returns an empty list so callers can splat the result unconditionally.
    """
    if n <= 0:
        return []
    return [Text("") for _ in range(n)]


def _render_tokens(
    tokens: list[Token],
    start: int,
    theme: dict[str, Any],
    *,
    depth: int = 0,
    columns: int | None = None,
) -> tuple[list[Element], int]:
    """Walk ``tokens`` from ``start`` and render each top-level block.

    Returns the list of rendered ``Element``\\ s (with blank-row
    spacers already inserted between adjacent blocks per the
    ``spacing_before_*`` / ``spacing_after_*`` theme knobs) and the
    index of the next unprocessed token (which is ``len(tokens)`` when
    called at the top level, or the index past the closing token of a
    sub-block when called recursively).

    ``depth`` tracks the list-nesting depth so :func:`_render_list` can
    pick the right marker style (``decimal`` / ``alpha`` / ``roman`` /
    cycling bullet chars). Top-level calls default to ``depth=0``; each
    recursive descent into a nested list passes ``depth + 1``.

    ``columns`` is the available content width for width-aware blocks
    (tables + the recursive blockquote / list paths that thread it
    further inward). ``None`` means "no width constraint known" —
    tables render at their ideal widths without shrinking. Both the
    static and reactive ``Markdown`` paths route through
    :func:`_render_markdown_to_string`, which threads the layout-time
    width (or viewport fallback) here.

    PR3: spacing is computed here (not via a flat ``gap=1`` on the
    outer ``Box``) so per-block gaps can vary — a heading gets a
    2-blank-row trailing gap, a paragraph gets 1, etc. The gap between
    two adjacent blocks is ``max(spacing_after_<prev>,
    spacing_before_<next>)`` so whichever block wants more space wins.
    """
    elements: list[Element] = []
    prev_block_type: str | None = None
    i = start
    while i < len(tokens):
        token = tokens[i]
        ttype = token.type

        block_type: str | None = None
        el: Element | None = None

        if ttype == "heading_open":
            # heading_open / inline / heading_close
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            if inline is not None and inline.type == "inline":
                el = _render_heading(token, inline, theme)
                block_type = "heading"
            # Skip heading_open + inline + heading_close.
            i += 3
        elif ttype == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            if inline is not None and inline.type == "inline":
                el = _render_paragraph(inline, theme)
                block_type = "paragraph"
            i += 3
        elif ttype in ("fence", "code_block"):
            el = _render_fence(token, theme)
            block_type = "code_block"
            i += 1
        elif ttype == "blockquote_open":
            el, next_i = _render_blockquote(tokens, i, theme, columns=columns)
            block_type = "blockquote"
            i = next_i
        elif ttype in ("bullet_list_open", "ordered_list_open"):
            el, next_i = _render_list(
                tokens, i, theme, depth=depth, columns=columns
            )
            block_type = "list"
            i = next_i
        elif ttype == "hr":
            hr_color = theme.get("hr_color")
            el = Divider(color=hr_color)
            block_type = "hr"
            i += 1
        elif ttype == "table_open":
            el, next_i = _render_table(tokens, i, theme, columns=columns)
            block_type = "table"
            i = next_i
        elif ttype == "inline":
            # A bare inline token at the top level (no paragraph wrapper).
            el = _render_paragraph(token, theme)
            block_type = "paragraph"
            i += 1
        else:
            # Anything else (html_block, paragraph_close, list_item_close,
            # …) is structural noise from a sub-block — skip silently.
            i += 1
            continue

        if el is None:
            continue

        # Insert the inter-block gap *before* this block based on the
        # previous block's ``spacing_after`` + this block's
        # ``spacing_before``. The first block (``prev_block_type is
        # None``) gets no leading gap so documents don't start with a
        # blank row.
        if prev_block_type is not None and block_type is not None:
            gap = _block_spacing(theme, prev_block_type, block_type)
            elements.extend(_spacer_rows(gap))

        elements.append(el)
        if block_type is not None:
            prev_block_type = block_type

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
    from ink.core.reconciler import Reconciler
    from ink.layout import layout, render_layout_to_string

    tokens = _parse(text)
    elements, _ = _render_tokens(tokens, 0, theme, columns=columns)
    # PR3: inter-block spacing is computed inside ``_render_tokens``
    # (per-block ``spacing_before_*`` / ``spacing_after_*`` theme knobs)
    # rather than via a flat ``gap=1`` on the outer Box, so a heading
    # can ask for a 2-row trailing gap while a paragraph asks for 1.
    inner = create_element("box", *elements, flexDirection="column")
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
    :func:`ink.components.Transform` uses to capture a snapshot of a
    sub-tree at mount time; here we use it at layout time so the
    snapshot refreshes whenever the source signal writes.
    """
    source: str | Signal[str] | Callable[[], str] = props["source"]
    theme: dict[str, Any] = props["theme"]
    box_props: dict[str, Any] = props["box_props"]

    from ink.hooks._runtime import _get_current_instance
    from ink.layout._text_width_context import get_current_text_width

    def render_reactive() -> str:
        # Resolve the source at layout time so a Signal read here
        # establishes a subscription inside the render-loop effect's
        # tracking context.
        text = _resolve_source(source)
        if not text:
            return ""
        # Prefer the layout-time measurement width (the actual content
        # box the parent grants this Text leaf) over the viewport
        # width. Without this, the snapshot is rendered at the
        # viewport width and then placed inside a narrower parent —
        # the pre-rendered box-drawing characters cannot be re-wrapped
        # by the layout engine and the parent's border scrambles. The
        # context width is set by the layout pass around the deferred
        # renderer invocation (see ``_layout_node``'s text branch);
        # ``None`` means the layout is measuring under unbounded width
        # (e.g. the very first subscription layout), in which case we
        # fall back to the viewport width.
        columns = get_current_text_width()
        if columns is None or columns < 1:
            inst = _get_current_instance()
            columns = 80
            if inst is not None:
                cols_attr = getattr(inst, "columns", 0)
                if isinstance(cols_attr, int) and cols_attr > 0:
                    columns = cols_attr
        return _cached_render(text, columns, theme)

    box_props = dict(box_props)
    box_props.pop("flexDirection", None)
    # PR3: no ``gap=1`` here — spacing is computed inside
    # :func:`_render_tokens` via per-block ``spacing_before_*`` /
    # ``spacing_after_*`` theme knobs (see :func:`_render_markdown_to_string`).
    return Box(
        Text(render_reactive),
        flexDirection="column",
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
        * :class:`ink.Signal` ``[str]`` — reactive. Each ``.value``
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
        A function component element whose ``type`` is
        :func:`_MarkdownImpl`. Both static and reactive sources share
        this path so width-aware blocks (tables) can pick up the
        layout-time content width for responsive shrink + key-value
        fallback. The function component body returns a ``Box`` whose
        only child is a single ``Text`` leaf carrying a callable that
        parses + renders the Markdown at layout time.

    Raises
    ------
    ImportError
        If :mod:`markdown_it` is not installed. The error message
        points the caller at ``pip install ink[markdown]``.

    Supported Markdown elements
    ---------------------------
    * Headings (``h1``-``h6``) with per-level colour + bold.
    * Paragraphs (plain ``Text`` leaves).
    * Inline emphasis (bold / italic / strikethrough — the last needs
      ``mdit-py-plugins`` or a Markdown-it preset that enables it).
    * Inline code (single-colour ``Text`` segment).
    * Links (rendered via OSC 8 hyperlinks; see
      :func:`ink.externals.link._wrap_osc8`).
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
    * Tables (bordered frame, column alignment from ``:---:`` markers,
      inline cell styling, responsive shrink to the available content
      width, and key-value fallback when the terminal is too narrow).
    * Soft / hard line breaks inside paragraphs.

    Usage
    -----
    ::

        Markdown("# Title\\n\\nSome **bold** text.")
        Markdown(my_signal_buffer)
        Markdown(source, theme={"h1_color": "cyan"})
    """
    effective_theme = _merge_theme(theme)

    # Eagerly verify the optional dependency is installed so the
    # caller gets a clear error at call time rather than at first
    # render. Both the static and reactive paths route through
    # :func:`_MarkdownImpl` (which calls :func:`_parse`), so the guard
    # lives here once rather than duplicating per branch.
    try:
        from markdown_it import MarkdownIt  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Markdown requires markdown-it-py. "
            "Install: pip install ink[markdown]"
        ) from exc

    # Strip ``flexDirection`` from the caller's box_props — the
    # component's contract is "one block per row", so the outer Box is
    # always ``"column"``. Doing this here (rather than only inside
    # :func:`_MarkdownImpl`) keeps ``el.props["box_props"]`` clean for
    # introspection and avoids a confusing "I passed row but got column"
    # surprise at render time.
    box_props = dict(box_props)
    box_props.pop("flexDirection", None)

    # Both static (``str``) and reactive (``Signal`` / ``Callable``)
    # sources route through :func:`_MarkdownImpl` so width-aware blocks
    # (currently tables — see :func:`_render_table`) can pick up the
    # layout-time width via ``get_current_text_width()``. The static
    # path used to return a ``box`` host element eagerly; the
    # ``Text(callable)`` shape defers parsing + rendering to layout
    # time, which is the only point at which the available content
    # width is known. This matches claude-code's ``MarkdownText`` model
    # and lets tables responsively shrink + degrade to key-value in
    # narrow containers (see ``research/layout-width-context.md``).
    return create_element(
        _MarkdownImpl,
        source=source,
        theme=effective_theme,
        box_props=box_props,
    )
