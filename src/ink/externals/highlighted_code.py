"""``HighlightedCode`` — Pygments-driven syntax highlighting (Phase 3 PR2).

Mirrors :mod:`ink-syntax-highlight`: turn a code string into a tree of
row ``Box`` elements (one per source line). Each row carries ONE
``Text`` leaf whose body is a single ANSI-coded string carrying inline
SGR sequences for every Pygments token on that source line — CC's
``<Text><Ansi>{code}</Ansi></Text>`` parity. The architectural
refactor (one Text leaf per source line, not one Text per token) is
the fix for the long-line shrink bug: previously each token was a
flexible child of the row Box, so when a row exceeded ``columns`` the
flex shrink algorithm penalised every token proportionally and ate
trailing characters from every Pygments token (``print`` → ``pri``,
``item['code']`` → ``it['co'``). Now the entire source line is one
flexible child; when it overflows ``columns`` the layout engine's
``_measure_paragraph → wrap_text(mode="wrap")`` pipeline wraps the
single leaf onto subsequent visual rows (with the existing ANSI-aware
word-break / hard-break logic) and continuation rows self-align under
the first row's code start column. Zero factory-level wrap code.

Pygments is an *optional* dependency. The factory ``import``\\ s it lazily
inside the function body; if the package is missing we raise an
``ImportError`` whose message points the caller at the right extra
(``pip install ink[highlight]``). Nothing in the rest of PyInk
imports Pygments, so the optional group only matters when this
component is actually used.

Design (per PRD PR2 scope):

* ``HighlightedCode`` is a **declarative** factory (like
  :func:`Divider`). It returns a ``box`` host element directly — no
  function component, no hooks, no live render pipeline needed. This
  is the cheapest possible shape and matches the use case: callers
  typically pass static strings; for reactive code, wrap the call site
  in a parent that re-renders.
* Tokenise once with :func:`pygments.lex`, walk each ``(token_type,
  value)`` pair, look up the colour in ``theme`` (most-specific
  Pygments token path wins; see :func:`_lookup_color`), and emit
  per-token SGR sequences wrapped around the token value. Token
  values can contain newlines (docstrings, multi-line comments) — we
  split on ``\\n`` so each physical line ends up in its own row
  ``Box``. The SGR sequences are concatenated into a single ANSI
  string per source line via :func:`tokens_to_ansi_string`.
* ``language="text"`` (the default) skips Pygments entirely and emits
  a plain string per source line (no SGR sequences) — there is no
  point tokenising plain text and it gives callers an obvious "off
  switch". ``language="auto"`` defers to
  :func:`pygments.lexers.guess_lexer`, which needs a few
  representative lines to be reliable.
* ``line_numbers=True`` prepends a dim right-aligned gutter to each
  row. We use ``dimColor=True`` rather than a colour so the gutter
  blends in regardless of theme.

Colour mapping: Pygments token types form a hierarchy
(``Token.Literal.String.Double`` is a child of ``Token.Literal.String``
is a child of ``Token.Literal`` is a child of ``Token``). The default
:data:`DEFAULT_THEME` is keyed on the **short** forms (``"String"``,
``"Number"``, …) that match the *content* of the Pygments path minus
the leading ``Token.`` prefix and the ``Literal.`` segment — Pygments
puts most interesting leaves under ``Token.Literal.*`` but exposes
them in documentation as just ``String`` / ``Number``, so we treat
``"Literal."`` as a synonym for ``""`` when matching. This means a
theme key of ``"String"`` matches both ``Token.Literal.String`` and
``Token.Literal.String.Double`` (via :func:`_lookup_color`'s walk-up).

The PRD's example theme used ``"brightBlack"`` as the comment colour;
PyInk's named-colour table (see :data:`ink.render.ansi.NAMED_COLORS`)
spells that ``"gray"`` / ``"grey"`` / ``"blackBright"`` instead. We
use ``"gray"`` so the mapping actually resolves; the visual result is
identical (SGR code 90).

PR2 scope: ships ``HighlightedCode`` only. Markdown integration lands
in PR3/PR4; StructuredDiff in PR5.
"""

from __future__ import annotations

from typing import Any

from ink.components.box import Box
from ink.components.text import Text
from ink.core.element import Element
from ink.render.ansi import _SGR_RESET, _sgr, parse_color

__all__ = ["HighlightedCode", "DEFAULT_THEME", "tokens_to_ansi_string"]

#: Default Pygments-token → PyInk-colour mapping. Keys are the *short*
#: forms of Pygments token paths (``"Keyword"``, ``"Name.Function"``,
#: ``"Literal.String"`` / ``"String"``, …). A value of ``None`` means
#: "use the terminal default" — emitted as a plain ``Text`` with no
#: ``color`` prop. See module docstring for the matching rules.
#:
#: Colours use PyInk's named-colour vocabulary
#: (see :data:`ink.render.ansi.NAMED_COLORS`):
#:
#: * ``"gray"`` (SGR 90) — the PRD called this ``"brightBlack"``; the
#:   named-colour table spells it ``"gray"`` / ``"blackBright"``.
#: * ``"red"`` / ``"green"`` / ``"yellow"`` / ``"blue"`` /
#:   ``"magenta"`` / ``"cyan"`` — the standard ANSI foregrounds.
DEFAULT_THEME: dict[str, str | None] = {
    # ---- Keyword --------------------------------------------------------
    "Keyword": "magenta",
    "Keyword.Constant": "magenta",
    "Keyword.Declaration": "magenta",
    "Keyword.Namespace": "magenta",
    "Keyword.Pseudo": "magenta",
    "Keyword.Reserved": "magenta",
    "Keyword.Type": "magenta",
    # ---- Name -----------------------------------------------------------
    "Name": None,
    "Name.Builtin": "cyan",
    "Name.Function": "blue",
    "Name.Class": "yellow",
    "Name.Exception": "red",
    "Name.Decorator": "yellow",
    "Name.Variable": "blue",
    # ---- String ---------------------------------------------------------
    "String": "green",
    "String.Affix": "green",
    "String.Doc": "gray",
    "String.Escape": "red",
    "String.Interpol": "green",
    # ---- Number ---------------------------------------------------------
    "Number": "cyan",
    "Number.Float": "cyan",
    "Number.Hex": "cyan",
    "Number.Integer": "cyan",
    # ---- Comment --------------------------------------------------------
    "Comment": "gray",
    "Comment.Preproc": "magenta",
    "Comment.Special": "gray",
    # ---- Operator -------------------------------------------------------
    "Operator": "red",
    "Operator.Word": "magenta",
    # ---- Punctuation ----------------------------------------------------
    "Punctuation": None,
    # ---- Text -----------------------------------------------------------
    "Text": None,
    "Text.Whitespace": None,
    # ---- Catch-alls -----------------------------------------------------
    "Error": "red",
    "Other": None,
}


def _normalize_path(path: str) -> str:
    """Strip the ``Token.`` prefix from a stringified Pygments token type.

    Pygments stringifies ``Token.Keyword.Declaration`` as
    ``"Token.Keyword.Declaration"``; we drop the leading ``Token.`` so
    theme keys can be the short forms (``"Keyword.Declaration"``). The
    bare ``"Token"`` root collapses to the empty string.
    """
    if path.startswith("Token."):
        return path[len("Token.") :]
    if path == "Token":
        return ""
    return path


def _candidate_paths(path: str) -> list[str]:
    """Yield progressively shorter prefixes of a Pygments token path.

    Pygments token types form a hierarchy (``Keyword.Declaration`` is
    a child of ``Keyword`` is a child of the root). We probe
    progressively shorter prefixes against ``theme``: the most specific
    key wins. ``Literal.`` is treated as a no-op prefix — Pygments
    nests most interesting leaves under ``Token.Literal.*`` but the
    docs and the PRD's example theme use the short forms
    (``String`` / ``Number``), so we additionally probe the path with
    ``Literal.`` stripped. Both probe orders are interleaved
    most-specific-first.
    """
    if not path:
        return [""]
    parts = path.split(".")
    out: list[str] = []
    for i in range(len(parts), 0, -1):
        out.append(".".join(parts[:i]))
    # If the path starts with ``Literal.``, also probe the path with
    # that prefix stripped (e.g. ``Literal.String.Double`` → also
    # ``String.Double`` / ``String``). This lets theme keys spelled
    # ``"String"`` / ``"Number"`` (per the PRD example and Pygments's
    # own documentation conventions) match the underlying Literal.*
    # tokens.
    if parts[0] == "Literal" and len(parts) > 1:
        stripped = parts[1:]
        for i in range(len(stripped), 0, -1):
            key = ".".join(stripped[:i])
            if key not in out:
                out.append(key)
    out.append("")
    return out


def _lookup_color(token_type: Any, theme: dict[str, str | None]) -> str | None:
    """Walk a Pygments token type from most-specific to most-generic.

    Probes :func:`_candidate_paths` against ``theme``; the first hit
    wins. ``None`` is a legitimate "use the terminal default" value,
    so a hit can return ``None`` — callers must distinguish *miss*
    (keep walking) from *hit-with-None* (stop and emit a plain Text).
    """
    path = _normalize_path(str(token_type))
    for key in _candidate_paths(path):
        if key in theme:
            return theme[key]
    return None


def _fg_sgr(color: str | None) -> str:
    """Return the foreground CSI-SGR sequence for ``color`` or ``""``.

    Returns the empty string when ``color`` is falsy / unparseable so
    callers can concatenate unconditionally without emitting stray
    ``\\x1b[m`` shorthands (which some terminals treat as a reset).
    """
    if not color:
        return ""
    body = parse_color(color, type_="foreground")
    if not body:
        return ""
    return _sgr(body)


def _token_to_ansi(value: str, color: str | None) -> str:
    """Wrap a single token value in its foreground SGR sequence.

    ``color=None`` (terminal default) emits no SGR — the value is
    returned verbatim. Otherwise the value is wrapped as
    ``\\x1b[<fg>m<value>\\x1b[0m`` (one reset per token so adjacent
    tokens with different colours don't bleed).
    """
    open_seq = _fg_sgr(color)
    if not open_seq:
        return value
    return open_seq + value + _SGR_RESET


def tokens_to_ansi_string(
    tokens: list[tuple[Any, str]],
    theme: dict[str, str | None],
) -> str:
    """Convert a list of Pygments ``(token_type, value)`` pairs to one ANSI string.

    Each token is wrapped in its own foreground SGR sequence (looked up
    via :func:`_lookup_color`); a token whose colour resolves to
    ``None`` (terminal default) is emitted verbatim with no SGR. The
    fragments are concatenated into a single string carrying inline
    ANSI escapes — CC's ``<Ansi>{code}</Ansi>`` parity.

    Multi-line token values (values containing ``\\n``) are split on
    ``\\n`` and each fragment is processed with its own ANSI span so
    an SGR sequence from one line can't bleed across the newline onto
    the next (a stray ``\\x1b[0m`` after a newline would dim the wrong
    row). The fragments are rejoined with ``\\n`` so the caller still
    sees a single string but with per-line ANSI isolation.
    """
    parts: list[str] = []
    for token_type, value in tokens:
        color = _lookup_color(token_type, theme)
        if "\n" not in value:
            parts.append(_token_to_ansi(value, color))
            continue
        # Multi-line value: each fragment carries its own SGR span so
        # the reset at end-of-fragment doesn't leak onto the next line.
        fragments = value.split("\n")
        rendered = [_token_to_ansi(frag, color) if frag else frag for frag in fragments]
        parts.append("\n".join(rendered))
    return "".join(parts)


def _group_tokens_by_line(
    tokens: list[tuple[Any, str]],
    theme: dict[str, str | None],
) -> list[str]:
    """Re-flow a flat token list into per-line ANSI strings.

    Pygments may emit token values that contain newlines (docstrings,
    multi-line comments, the ``\\n`` whitespace tokens between source
    lines). We split each value on ``\\n`` and re-distribute the
    fragments across rows so the rendered output preserves the source's
    physical line structure. Empty trailing rows are dropped so a final
    newline doesn't produce a blank row at the bottom.

    Each returned entry is a single ANSI-coded string carrying inline
    SGR sequences for every Pygments token on that source line — the
    architectural refactor that fixes the long-line shrink bug (each
    source line becomes ONE ``Text`` leaf so the flex shrink algorithm
    no longer penalises per-token leaves).
    """
    rows: list[list[tuple[Any, str]]] = [[]]
    for token_type, value in tokens:
        # Fast path: value has no newline → single fragment, no split.
        if "\n" not in value:
            rows[-1].append((token_type, value))
            continue
        # Multi-line value: split on \n, emitting each fragment on the
        # current row and starting a new row at every newline. Empty
        # fragments (consecutive newlines, or a value starting with
        # newline) are skipped to avoid emitting empty Text leaves —
        # matches the language="text" fast path's
        # ``if token_rows and code.endswith("\n")`` trailing-row drop.
        fragments = value.split("\n")
        for i, frag in enumerate(fragments):
            if frag:
                rows[-1].append((token_type, frag))
            if i < len(fragments) - 1:
                rows.append([])
    # Drop a single trailing empty row that comes from a final newline.
    if rows and not rows[-1]:
        rows.pop()
    return [tokens_to_ansi_string(row, theme) for row in rows]


#: LRU cache for Pygments tokenisation. Keyed on ``(code, language)``.
#: The render-loop's subscription layout *and* the paint layout both
#: evaluate reactive ``Text`` callables, so without a cache a single
#: signal flush tokenises the same code block twice. Pygments lexing is
#: the dominant cost for highlighted code in a streaming Markdown
#: context, hence the memoisation. Bound to ``_TOKEN_CACHE_MAX`` entries
#: so a long-lived process streaming many distinct snippets does not
#: grow the cache unbounded.
_TOKEN_CACHE_MAX: int = 64
_token_cache: dict[tuple[str, str], list[tuple[Any, str]]] = {}


def _tokenize(
    code: str,
    language: str,
    pygments_module: Any,
    get_lexer_by_name: Any,
    guess_lexer: Any,
) -> list[tuple[Any, str]]:
    """Pygments lex with an LRU cache on ``(code, language)``.

    Resolved lexer/tokens functions are passed in by the caller so the
    lazy import inside :func:`HighlightedCode` stays the single point
    that establishes the Pygments dependency.
    """
    key = (code, language)
    cached = _token_cache.get(key)
    if cached is not None:
        _token_cache.pop(key)
        _token_cache[key] = cached
        return cached
    lexer = (
        guess_lexer(code)
        if language == "auto"
        else get_lexer_by_name(language)
    )
    value = list(pygments_module.lex(code, lexer))
    _token_cache[key] = value
    if len(_token_cache) > _TOKEN_CACHE_MAX:
        _token_cache.pop(next(iter(_token_cache)))
    return value


def _build_line_rows(
    rows: list[str],
    *,
    line_numbers: bool,
    indent: str = "",
    first_row_prefix: str = "",
    start_line: int = 1,
) -> list[Element]:
    """Wrap each per-line ANSI string in a row ``Box``.

    Each entry of ``rows`` is a single ANSI-coded string (the output of
    :func:`tokens_to_ansi_string` / the ``language="text"`` fast path's
    plain source line). The string is wrapped in ONE ``Text`` leaf so
    the source line becomes a single flexible child of the row Box —
    the architectural refactor that lets long lines wrap (via the
    layout engine's ``_measure_paragraph → wrap_text(mode="wrap")``
    path) instead of being shrunk by the flex algorithm. CC parity:
    CC's ``HighlightedCode`` is ``<Text><Ansi>{code}</Ansi></Text>``,
    one Text per source line, no per-token leaves.

    With ``line_numbers=True``, every row is prefixed with a dim
    right-aligned gutter. Numbering starts at 1 and the gutter is
    zero-padded to the width of the last line number so columns line
    up regardless of how many lines the snippet has.

    With a non-empty ``indent``, every continuation row is additionally
    prefixed with a plain ``Text`` leaf carrying that literal string.
    This lets a caller embed the code block under a ``⎿`` gutter
    (Jarvis's Edit/Write archived-row rendering): the first visual line
    shows the gutter glyph, the continuation lines line up under the
    body via the indent prefix. Mirrors Claude Code's ``MessageResponse``
    indentation pattern without nesting an extra Box layer.

    ``first_row_prefix`` (when non-empty) replaces ``indent`` on the
    first row only — used by callers that want the parent's ``⎿``
    glyph on the same visual line as the first body row. Continuation
    rows still use ``indent`` so all rows line up under the glyph's
    column. Empty (default) preserves the legacy behaviour where every
    row (including the first) uses ``indent``.

    ``start_line`` (default ``1``) shifts the gutter numbering so the
    first body row carries the 1-indexed source-file line the snippet
    begins at — mirrors :func:`StructuredDiff`'s ``start_line`` prop.
    Used by callers that know the real file position (Jarvis's Edit
    ``insert`` / ``append_section`` actions record this in
    ``ToolResult.metadata["start_line"]``). Default preserves the
    snippet-relative ``1, 2, 3, …`` sequence for backward compatibility.
    """
    indent_leaf = Text(indent) if indent else None
    first_row_leaf = Text(first_row_prefix) if first_row_prefix else None
    if not line_numbers:
        out_no_num: list[Element] = []
        for i, ansi_str in enumerate(rows):
            children: list[Element] = []
            # First row consumes the parent gutter prefix; subsequent
            # rows fall back to the continuation ``indent``.
            if i == 0 and first_row_leaf is not None:
                children.append(first_row_leaf)
            elif indent_leaf is not None:
                children.append(indent_leaf)
            children.append(Text(ansi_str))
            out_no_num.append(Box(*children, flexDirection="row"))
        return out_no_num
    total = len(rows)
    # Width of the largest line number, e.g. ``3`` for a 100-line
    # snippet. When ``start_line`` shifts the counter the largest number
    # is ``start_line + total - 1`` (last row of the snippet), so the
    # gutter is padded to that width to keep columns aligned across
    # snippets that begin mid-file. Plus a trailing space for visual
    # separation from the code. Zero-padded so digits stay column-aligned
    # across the 9→10 / 99→100 boundaries (``094``, ``095``, …, ``100``)
    # instead of right-aligning with spaces.
    last_line = max(1, int(start_line)) + total - 1 if total else max(1, int(start_line))
    gutter_width = max(1, len(str(last_line)))
    start = max(1, int(start_line))
    out: list[Element] = []
    for i, ansi_str in enumerate(rows, start=start):
        gutter = Text(f"{i:0{gutter_width}} ", dimColor=True)
        row_children: list[Element] = []
        # First row consumes the parent gutter prefix; subsequent rows
        # fall back to the continuation ``indent``.
        if i == start and first_row_leaf is not None:
            row_children.append(first_row_leaf)
        elif indent_leaf is not None:
            row_children.append(indent_leaf)
        row_children.append(gutter)
        row_children.append(Text(ansi_str))
        out.append(Box(*row_children, flexDirection="row"))
    return out


def HighlightedCode(
    code: str,
    *,
    language: str = "text",
    theme: dict[str, str | None] | None = None,
    line_numbers: bool = False,
    indent: str = "",
    first_row_prefix: str = "",
    start_line: int = 1,
    **box_props: Any,
) -> Element:
    """Render a code string with Pygments-driven syntax highlighting.

    Parameters
    ----------
    code:
        Source code to highlight. Leading / trailing newlines are
        preserved verbatim — callers should ``.strip()`` first if they
        want a tight fit. Multi-line token values (docstrings, block
        comments) are split across rows so the physical line structure
        of the source is honoured.
    language:
        Pygments lexer alias (``"python"`` / ``"javascript"`` /
        ``"sql"`` / ``"yaml"`` / ``"json"`` / …). ``"text"`` (default)
        skips highlighting and emits a plain ``Text`` body — useful for
        pre-formatted output that should not be colourised.
        ``"auto"`` defers to :func:`pygments.lexers.guess_lexer`,
        which needs a few representative lines to be reliable.
    theme:
        Override (or extend) the default :data:`DEFAULT_THEME`. Keys
        are Pygments token paths without the ``Token.`` prefix
        (``"Keyword"``, ``"Literal.String"``, ``"Name.Function"``, …).
        ``"String"`` and ``"Number"`` are accepted as aliases for
        ``"Literal.String"`` / ``"Literal.Number"``. A value of
        ``None`` resets the entry to the terminal default colour.
    line_numbers:
        When ``True``, prepend a dim right-aligned line-number gutter
        to each row. The gutter width is sized to the largest line
        number so columns line up regardless of snippet length.
    indent:
        Optional literal string prepended to every continuation row
        (default ``""``). Used by callers that embed the code block
        under a parent glyph (e.g. Jarvis's archived Write row: ``⎿``
        on the first visual line + 5-space indent on continuation
        lines so the code body lines up under the gutter). When
        ``first_row_prefix`` is also set, the first row uses
        ``first_row_prefix`` instead — see below.
    first_row_prefix:
        Optional literal string prepended to the *first* row in lieu
        of ``indent`` (default ``""``). Used by callers that want the
        parent's ``⎿`` glyph on the same visual line as the first body
        row (CC's ``MessageResponse`` pattern): pass
        ``first_row_prefix="  ⎿  "`` and the first row carries it;
        continuation rows still use ``indent`` so all rows line up
        under the glyph's column. Empty (default) preserves the
        legacy behaviour where every row (including the first) uses
        ``indent``.
    **box_props:
        Forwarded to the outer ``Box`` container (``flexDirection`` is
        always set to ``"column"`` and cannot be overridden — the
        component's contract is "one row per source line"). Useful
        props include ``borderStyle`` / ``padding`` / ``width`` /
        ``backgroundColor``.
    start_line:
        1-indexed source-file line where the snippet begins (default
        ``1``). When ``line_numbers=True`` the gutter renders real
        file line numbers (e.g. ``42``, ``43``, …) instead of the
        snippet-relative ``1, 2, 3, …`` sequence. Mirrors
        :func:`StructuredDiff`'s ``start_line`` prop so Jarvis's Edit
        ``insert`` / ``append_section`` actions — which route through
        HighlightedCode (empty ``before``) — can carry the same
        file-accurate gutter information as Edit ``str_replace``.
        Values ``<= 0`` are clamped to ``1`` defensively; non-int /
        missing falls back to the default.

    Returns
    -------
    Element
        A ``box`` host element (column of row ``Box`` elements, each
        containing a single ``Text`` leaf whose body is the ANSI-coded
        source line). No function component is involved — the factory
        is purely declarative, which makes
        ``Box(HighlightedCode(...), Text(...))`` safe to call from any
        context.

    Raises
    ------
    ImportError
        If :mod:`pygments` is not installed. The error message points
        the caller at ``pip install ink[highlight]``.

    Usage
    -----
    ::

        HighlightedCode("print('hi')", language="python")
        HighlightedCode(code, language="python", line_numbers=True,
                        borderStyle="round", padding=1)
    """
    # Plain-text fast path: no Pygments dependency, no colour lookup.
    # Split into rows directly so the line-number machinery is shared
    # with the highlighted branch (one row per source line, optional
    # numbered gutter). Each entry is a plain string (no SGR sequences)
    # so the single-Text-leaf path renders the line verbatim.
    if language in ("text", ""):
        token_rows = code.split("\n")
        # Drop a trailing empty row produced by a final newline so we
        # don't render a blank row at the bottom.
        if token_rows and code.endswith("\n"):
            token_rows = token_rows[:-1]
        line_rows = _build_line_rows(
            token_rows,
            line_numbers=line_numbers,
            indent=indent,
            first_row_prefix=first_row_prefix,
            start_line=start_line,
        )
        box_props.pop("flexDirection", None)
        return Box(*line_rows, flexDirection="column", **box_props)

    try:
        import pygments  # lazy: keeps ``pygments`` off the core path
        from pygments.lexers import get_lexer_by_name, guess_lexer
    except ImportError as exc:
        # Re-raise with the friendly "install the extra" message; the
        # original ``ImportError`` is chained via ``from`` so callers
        # can still inspect what went wrong.
        raise ImportError(
            "HighlightedCode requires pygments. "
            "Install: pip install ink[highlight]"
        ) from exc

    effective_theme: dict[str, str | None] = dict(DEFAULT_THEME)
    if theme:
        effective_theme.update(theme)

    # Tokenise once. ``_tokenize`` memoises on ``(code, language)`` so
    # a reactive Markdown that re-highlights the same code block on
    # every layout pass (the render loop runs layout twice per signal
    # flush — once for subscription tracking, once for the paint) does
    # not pay the Pygments lexing cost twice. This was a major
    # contributor to the Phase 3 "highlighted-code demo pins CPU" bug.
    tokens = _tokenize(code, language, pygments, get_lexer_by_name, guess_lexer)
    token_rows = _group_tokens_by_line(tokens, effective_theme)
    line_rows = _build_line_rows(
        token_rows,
        line_numbers=line_numbers,
        indent=indent,
        first_row_prefix=first_row_prefix,
        start_line=start_line,
    )

    # A column of row Boxes; each row Box holds the inline tokens for
    # one source line. ``flexDirection="column"`` is forced here even
    # if the caller passed a conflicting value via box_props — the
    # component's contract is one row per source line.
    box_props.pop("flexDirection", None)
    return Box(*line_rows, flexDirection="column", **box_props)
