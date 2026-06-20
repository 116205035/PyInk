"""``HighlightedCode`` — Pygments-driven syntax highlighting (Phase 3 PR2).

Mirrors :mod:`ink-syntax-highlight`: turn a code string into a tree of
``Text`` leaves, one per token, each carrying the colour mapped from the
token's Pygments type via the active :data:`theme`. The output is a
column of rows (one per source line); each row is a ``Box`` of inline
``Text`` tokens so adjacent tokens on the same line stay side-by-side.

Pygments is an *optional* dependency. The factory ``import``\\ s it lazily
inside the function body; if the package is missing we raise an
``ImportError`` whose message points the caller at the right extra
(``pip install pyink[highlight]``). Nothing in the rest of PyInk
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
  Pygments token path wins; see :func:`_lookup_color`), and emit a
  ``Text`` leaf. Token values can contain newlines (docstrings,
  multi-line comments) — we split on ``\\n`` so each physical line
  ends up in its own row ``Box``.
* ``language="text"`` (the default) skips Pygments entirely and emits
  a single plain ``Text`` body — there is no point tokenising plain
  text and it gives callers an obvious "off switch". ``language="auto"``
  defers to :func:`pygments.lexers.guess_lexer`, which needs a few
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
PyInk's named-colour table (see :data:`pyink.render.ansi.NAMED_COLORS`)
spells that ``"gray"`` / ``"grey"`` / ``"blackBright"`` instead. We
use ``"gray"`` so the mapping actually resolves; the visual result is
identical (SGR code 90).

PR2 scope: ships ``HighlightedCode`` only. Markdown integration lands
in PR3/PR4; StructuredDiff in PR5.
"""

from __future__ import annotations

from typing import Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element

__all__ = ["HighlightedCode", "DEFAULT_THEME"]

#: Default Pygments-token → PyInk-colour mapping. Keys are the *short*
#: forms of Pygments token paths (``"Keyword"``, ``"Name.Function"``,
#: ``"Literal.String"`` / ``"String"``, …). A value of ``None`` means
#: "use the terminal default" — emitted as a plain ``Text`` with no
#: ``color`` prop. See module docstring for the matching rules.
#:
#: Colours use PyInk's named-colour vocabulary
#: (see :data:`pyink.render.ansi.NAMED_COLORS`):
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


def _token_text(value: str, color: str | None) -> Element:
    """Build a ``Text`` leaf for a single token value.

    ``color=None`` is emitted as a plain ``Text`` (no SGR sequence);
    callers rely on this to honour the terminal default for tokens
    like ``Punctuation`` / ``Text``.
    """
    if color is None:
        return Text(value)
    return Text(value, color=color)


def _group_tokens_by_line(
    tokens: list[tuple[Any, str]],
    theme: dict[str, str | None],
) -> list[list[Element]]:
    """Re-flow a flat token list into per-line ``Text`` lists.

    Pygments may emit token values that contain newlines (docstrings,
    multi-line comments, the ``\\n`` whitespace tokens between source
    lines). We split each value on ``\\n`` and re-distribute the
    fragments across rows so the rendered output preserves the source's
    physical line structure. Empty trailing rows are dropped so a final
    newline doesn't produce a blank row at the bottom.
    """
    rows: list[list[Element]] = [[]]
    for token_type, value in tokens:
        color = _lookup_color(token_type, theme)
        # Fast path: value has no newline → single fragment, no split.
        if "\n" not in value:
            rows[-1].append(_token_text(value, color))
            continue
        # Multi-line value: split on \n, emitting each fragment on the
        # current row and starting a new row at every newline. Empty
        # fragments (consecutive newlines, or a value starting with
        # newline) are skipped to avoid emitting empty Text leaves.
        fragments = value.split("\n")
        for i, frag in enumerate(fragments):
            if frag:
                rows[-1].append(_token_text(frag, color))
            if i < len(fragments) - 1:
                rows.append([])
    # Drop a single trailing empty row that comes from a final newline.
    if rows and not rows[-1]:
        rows.pop()
    return rows


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
    rows: list[list[Element]],
    *,
    line_numbers: bool,
) -> list[Element]:
    """Wrap each per-line token list in a row ``Box``.

    With ``line_numbers=True``, every row is prefixed with a dim
    right-aligned gutter. Numbering starts at 1 and the gutter is
    zero-padded to the width of the last line number so columns line
    up regardless of how many lines the snippet has.
    """
    if not line_numbers:
        return [
            Box(*tokens, flexDirection="row") if tokens else Box(flexDirection="row")
            for tokens in rows
        ]
    total = len(rows)
    # Width of the largest line number, e.g. ``3`` for a 100-line
    # snippet. Plus a trailing space for visual separation from the
    # code. Right-aligned via Python format spec.
    gutter_width = max(1, len(str(total)))
    out: list[Element] = []
    for i, tokens in enumerate(rows, start=1):
        gutter = Text(f"{i:>{gutter_width}} ", dimColor=True)
        out.append(Box(gutter, *tokens, flexDirection="row"))
    return out


def HighlightedCode(
    code: str,
    *,
    language: str = "text",
    theme: dict[str, str | None] | None = None,
    line_numbers: bool = False,
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
    **box_props:
        Forwarded to the outer ``Box`` container (``flexDirection`` is
        always set to ``"column"`` and cannot be overridden — the
        component's contract is "one row per source line"). Useful
        props include ``borderStyle`` / ``padding`` / ``width`` /
        ``backgroundColor``.

    Returns
    -------
    Element
        A ``box`` host element (column of row ``Box`` elements, each
        containing the per-token ``Text`` leaves for that source line).
        No function component is involved — the factory is purely
        declarative, which makes ``Box(HighlightedCode(...), Text(...))``
        safe to call from any context.

    Raises
    ------
    ImportError
        If :mod:`pygments` is not installed. The error message points
        the caller at ``pip install pyink[highlight]``.

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
    # numbered gutter).
    if language in ("text", ""):
        token_rows = [[Text(line)] for line in code.split("\n")]
        # Drop a trailing empty row produced by a final newline so we
        # don't render a blank row at the bottom.
        if token_rows and code.endswith("\n"):
            token_rows.pop()
        line_rows = _build_line_rows(token_rows, line_numbers=line_numbers)
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
            "Install: pip install pyink[highlight]"
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
    line_rows = _build_line_rows(token_rows, line_numbers=line_numbers)

    # A column of row Boxes; each row Box holds the inline tokens for
    # one source line. ``flexDirection="column"`` is forced here even
    # if the caller passed a conflicting value via box_props — the
    # component's contract is one row per source line.
    box_props.pop("flexDirection", None)
    return Box(*line_rows, flexDirection="column", **box_props)
