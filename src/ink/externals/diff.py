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

CC-alignment extensions (07-20-tool-message-rendering-polish):

Three new optional props opt into Claude Code's visual signatures:

* ``line_numbers=True`` prefixes each body row with a padded line-number
  gutter (``<padded_num><sigil><space>``). The numbering is a CC-style
  row counter that increments on every add / context row and stays
  unchanged on remove rows (mirrors CC's ``Fallback.tsx:423``).
* ``inline_highlight=True`` pairs adjacent ``-`` / ``+`` lines and runs
  :class:`difflib.SequenceMatcher` on their whitespace tokens. When the
  change ratio is below CC's ``0.4`` threshold, only the changed tokens
  are coloured in the brighter ``add_color`` / ``del_color`` (matching
  CC's ``diffAddedWord`` / ``diffRemovedWord`` semantics).
* ``full_width_bg=True`` sets ``flushBackgroundToWidth=True`` on the
  outer Box of every add / del row so the per-row colour band spans
  edge-to-edge across the terminal width (CC's strongest diff signature).

All three default to ``False``; existing callers see no optical change.

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


# CC's threshold for when word-level diffing falls back to whole-line
# colouring (see ``Fallback.tsx:80`` — ``CHANGE_THRESHOLD = 0.4``). Above
# this ratio the lines are considered "too different" and the inline
# highlight is suppressed so the reader sees a uniform coloured band
# instead of a noisy mix of bright / dim tokens.
_INLINE_CHANGE_THRESHOLD = 0.4


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


def _tokenize_for_word_diff(line: str) -> list[tuple[str, str]]:
    """Split ``line`` into ``(separator, token)`` pairs.

    Mirrors CC's ``diffWordsWithSpace`` (the ``"diff"`` npm package) —
    each token carries its leading whitespace so the reconstructed line
    preserves the original spacing. ``difflib.SequenceMatcher`` is run
    over the concatenated token strings (separator + token), so a match
    keeps separator + token together and a change marks the whole pair.

    Returns an empty list for an empty / whitespace-only line so the
    caller can short-circuit (no tokens to highlight).
    """
    if not line:
        return []
    tokens: list[tuple[str, str]] = []
    # ``re.finditer`` would work but ``str.split`` keeps the separators
    # out of the result; we walk the string manually so each token
    # carries its leading whitespace. This is exactly what CC's
    # ``diffWordsWithSpace`` does at a higher level.
    i = 0
    n = len(line)
    while i < n:
        # Eat leading whitespace into the next token's separator.
        sep_start = i
        while i < n and line[i].isspace():
            i += 1
        sep = line[sep_start:i]
        # Read the non-whitespace body.
        tok_start = i
        while i < n and not line[i].isspace():
            i += 1
        tok = line[tok_start:i]
        if not sep and not tok:
            # Both empty — defensive guard against an infinite loop
            # that should never trigger given the loop conditions.
            break
        tokens.append((sep, tok))
    return tokens


def _word_diff_parts(
    old_line: str,
    new_line: str,
) -> tuple[list[tuple[str, str, bool]], float] | None:
    """Compute per-token change markers for two adjacent diff lines.

    Returns ``None`` when the change ratio exceeds
    :data:`_INLINE_CHANGE_THRESHOLD` (CC's 0.4 cut-off) so the caller
    falls back to whole-line colouring. Otherwise returns a list of
    ``(separator, token, changed)`` tuples — one per token of the
    *target* line — plus the change ratio for callers that want to
    inspect it.

    The ``changed`` flag is ``True`` when the token differs between the
    old / new line; ``False`` when the token is common (unchanged). The
    caller renders changed tokens in the brighter colour and unchanged
    tokens inherit the row's diff colour.

    Mirrors CC's ``generateWordDiffElements`` (``Fallback.tsx:237``):
    ``changeRatio = sum(len(changed_tokens)) / (len(old) + len(new))``
    and rejects the pair when the ratio exceeds ``0.4``. We compute
    the changed length via :class:`difflib.SequenceMatcher.get_opcodes`
    so a token + its leading whitespace move together (CC's
    ``diffWordsWithSpace`` semantics).
    """
    old_tokens = _tokenize_for_word_diff(old_line)
    new_tokens = _tokenize_for_word_diff(new_line)
    if not old_tokens and not new_tokens:
        return None
    total_chars = len(old_line) + len(new_line)
    if total_chars == 0:
        return None

    # ``SequenceMatcher`` on the concatenated ``sep+token`` strings so
    # a token + its leading whitespace move together.
    old_keys = [sep + tok for sep, tok in old_tokens]
    new_keys = [sep + tok for sep, tok in new_tokens]
    matcher = difflib.SequenceMatcher(a=old_keys, b=new_keys, autojunk=False)

    # Walk the opcodes and mark which target (new) tokens differ from
    # the source (old). ``equal`` / ``replace`` / ``delete`` / ``insert``
    # are the four opcodes; ``equal`` marks unchanged tokens, every
    # other opcode marks changed tokens.
    parts: list[tuple[str, str, bool]] = []
    changed_chars = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for sep, tok in new_tokens[j1:j2]:
                parts.append((sep, tok, False))
        else:
            for sep, tok in new_tokens[j1:j2]:
                parts.append((sep, tok, True))
                changed_chars += len(tok)
    change_ratio = changed_chars / total_chars
    if change_ratio > _INLINE_CHANGE_THRESHOLD:
        return None
    return parts, change_ratio


def _build_inline_highlighted_spans(
    parts: list[tuple[str, str, bool]],
    *,
    base_color: str,
    inline_color: str,
    bg_color: str | None,
    full_width_bg: bool = False,
) -> list[Element]:
    """Render a row's body as a list of inline-highlighted ``Text`` spans.

    Each part carries its separator (leading whitespace) and token. The
    ``changed`` flag selects between the brighter ``inline_color`` and
    the row's ``base_color``. Whitespace separators inherit the
    surrounding token's colour so the row reads as a continuous coloured
    band.

    Returns a flat list of ``Text`` leaves so the caller can pack them
    as siblings inside a row ``Box``. We deliberately avoid nesting
    ``Text`` inside ``Text`` because PyInk's text host flattens nested
    children into the outer leaf's style — losing per-token colours.
    """
    spans: list[Element] = []
    for sep, tok, changed in parts:
        if sep:
            sep_props: dict[str, Any] = {"color": base_color}
            if bg_color is not None:
                sep_props["backgroundColor"] = bg_color
                if full_width_bg:
                    sep_props["flushBackgroundToWidth"] = True
            spans.append(Text(sep, **sep_props))
        if not tok:
            continue
        if changed:
            tok_props: dict[str, Any] = {
                "color": inline_color,
                "bold": True,
            }
            if bg_color is not None:
                tok_props["backgroundColor"] = bg_color
                if full_width_bg:
                    tok_props["flushBackgroundToWidth"] = True
            spans.append(Text(tok, **tok_props))
        else:
            base_props: dict[str, Any] = {"color": base_color}
            if bg_color is not None:
                base_props["backgroundColor"] = bg_color
                if full_width_bg:
                    base_props["flushBackgroundToWidth"] = True
            spans.append(Text(tok, **base_props))
    if not spans:
        # Edge case: both lines are empty / whitespace only. Emit a
        # single empty Text so the row still occupies a line.
        spans.append(Text("", color=base_color))
    return spans


def _line_number_gutter(
    line_num: int | None,
    *,
    width: int,
    sigil: str,
    color: str,
    bg_color: str | None = None,
    full_width_bg: bool = False,
) -> Text:
    """Build a CC-style line-number gutter ``Text`` leaf.

    Format: ``<padded_num><sigil>`` where the padded number uses
    ``width`` characters (right-aligned). When ``line_num`` is ``None``
    (the continuation row of a soft-wrapped pair) the entire number
    column is rendered as spaces. CC's ``Fallback.tsx:331`` produces the
    same shape via ``padStart(maxWidth)``.
    """
    if line_num is None:
        num_str = " " * width
    else:
        num_str = str(line_num).rjust(width)
    body = f"{num_str}{sigil}"
    props: dict[str, Any] = {"color": color}
    if bg_color is not None:
        props["backgroundColor"] = bg_color
        # Per-leaf ``flushBackgroundToWidth`` mirrors the row Box flag so
        # the bg band fills the terminal width regardless of which leaf
        # the renderer visits first. See :func:`_render_diff_row_cc`
        # for the full explanation.
        if full_width_bg:
            props["flushBackgroundToWidth"] = True
    return Text(body, **props)


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


def _render_diff_row_cc(
    *,
    body: str,
    sigil: str,
    base_color: str,
    inline_parts: list[tuple[str, str, bool]] | None,
    inline_color: str,
    bg_color: str | None,
    full_width_bg: bool,
    line_num: int | None,
    gutter_width: int,
    indent: str = "",
    row_prefix: str = "",
) -> Element:
    """Render a single add / del / context row in CC-alignment mode.

    Combines the optional line-number gutter, optional inline highlight,
    and optional full-width background into a single row ``Box``. This
    path is used when at least one of ``line_numbers`` /
    ``inline_highlight`` / ``full_width_bg`` is enabled; the legacy
    :func:`_render_diff_line` fast path handles the no-CC-features case.

    Parameters
    ----------
    body:
        The code portion (diff line with its ``+`` / ``-`` / `` `` prefix
        stripped).
    sigil:
        ``"+"`` / ``"-"`` / ``" "`` — the diff marker shown right after
        the line-number gutter.
    base_color:
        Diff colour (``add_color`` / ``del_color``) applied to unchanged
        tokens and the sigil.
    inline_parts:
        Output of :func:`_word_diff_parts` when inline highlighting is
        enabled for this row and the change ratio was below threshold.
        ``None`` means "fall back to whole-line colour" — used either
        when inline highlighting is off, the row is unpaired, or the
        word-diff threshold rejected the pair.
    inline_color:
        Brighter colour (``greenBright`` / ``redBright``) applied to
        changed tokens when ``inline_parts`` is not ``None``.
    bg_color:
        Optional per-row background colour. Required for full-width bg.
    full_width_bg:
        When ``True`` the outer Box carries
        ``flushBackgroundToWidth=True`` so the bg spans the terminal
        width.
    line_num:
        Optional 1-indexed row number rendered in the gutter.
        ``None`` suppresses the number (renders spaces).
    gutter_width:
        Width (in characters) the line-number column is right-aligned
        to. ``0`` disables the gutter entirely.
    indent:
        Optional literal string prepended to continuation rows (default
        ``""``). Used by callers that embed the diff under a parent
        glyph (e.g. Jarvis's archived Edit row: ``⎿`` on the first
        visual line + matching indent on every continuation row so the
        diff lines up under the gutter). The caller picks which row is
        the "first" by passing a non-empty ``row_prefix`` for it; rows
        that don't get ``row_prefix`` fall back to ``indent``.
    row_prefix:
        Optional literal string prepended to *this* row in lieu of
        ``indent`` (default ``""``). The caller uses this to attach the
        parent's ``⎿`` glyph to the first body row — see
        :func:`_render_diff` for the first-row wiring.

    Returns
    -------
    Element
        A row ``Box`` (gutter + body) or a single ``Text`` leaf when no
        CC features are active for this row.
    """
    children: list[Element] = []

    # Row prefix (parent gutter alignment). When the caller passes a
    # non-empty ``row_prefix`` (e.g. Jarvis's ``"  ⎿  "`` parent gutter)
    # it replaces the default ``indent`` on this row; otherwise we use
    # ``indent`` for continuation-row alignment.
    prefix = row_prefix if row_prefix else indent
    if prefix:
        children.append(Text(prefix))

    # Gutter (line number + sigil). Width 0 means caller opted out.
    if gutter_width > 0:
        children.append(
            _line_number_gutter(
                line_num,
                width=gutter_width,
                sigil=sigil,
                color=base_color,
                bg_color=bg_color,
                full_width_bg=full_width_bg and bg_color is not None,
            )
        )

    # Body: inline-highlighted spans when available, otherwise a plain
    # Text carrying the full body in the diff colour.
    if inline_parts is not None:
        children.extend(
            _build_inline_highlighted_spans(
                inline_parts,
                base_color=base_color,
                inline_color=inline_color,
                bg_color=bg_color,
                full_width_bg=full_width_bg and bg_color is not None,
            )
        )
    else:
        body_props: dict[str, Any] = {"color": base_color}
        if bg_color is not None:
            body_props["backgroundColor"] = bg_color
            # ``full_width_bg=True``: per-leaf ``flushBackgroundToWidth``
            # so the bg SGR fills the row's full terminal width via the
            # row-level painter (CC ``Fallback.tsx:334`` signature).
            # Setting it on the outer Box alone is insufficient — the
            # ANSI bg fill needs to be carried by each Text leaf that
            # paints a cell, otherwise the row-level painter runs before
            # the leaf paints and the leaf's own bg SGR (via the legacy
            # wrap path) overwrites the flushed band with a text-width
            # band. Per-leaf flush is the single source of truth.
            if full_width_bg:
                body_props["flushBackgroundToWidth"] = True
        children.append(Text(body, **body_props))

    # No gutter + plain body + no bg → collapse to a single Text so we
    # don't add a Box wrapper for the legacy no-features case.
    if not children:
        return Text(body, color=base_color)

    box_props: dict[str, Any] = {"flexDirection": "row"}
    if full_width_bg and bg_color is not None:
        box_props["backgroundColor"] = bg_color
        box_props["flushBackgroundToWidth"] = True
    return Box(*children, **box_props)


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


def _classify_diff_lines(diff_lines: list[str]) -> list[dict[str, Any]]:
    """Turn ``unified_diff`` output into a list of typed entries.

    Each entry is a dict with:

    * ``kind``: ``"hunk"`` / ``"marker"`` / ``"add"`` / ``"del"`` /
      ``"context"``.
    * ``body``: the line content with the leading prefix stripped
      (``""`` for hunk / marker kinds; the code / text for the others).
    * ``raw``: the original diff line (kept for hunk / marker rendering).

    The body rows preserve their original order so the renderer can
    walk them once for line numbering and once for output. Hunk and
    marker entries are skipped by the line counter (they don't
    correspond to source rows).
    """
    entries: list[dict[str, Any]] = []
    for line in diff_lines:
        if line.startswith("@@"):
            entries.append({"kind": "hunk", "raw": line, "body": ""})
        elif line.startswith("+++") or line.startswith("---"):
            entries.append({"kind": "marker", "raw": line, "body": ""})
        elif line.startswith("+"):
            entries.append({"kind": "add", "raw": line, "body": line[1:]})
        elif line.startswith("-"):
            entries.append({"kind": "del", "raw": line, "body": line[1:]})
        else:
            # Context line — leading space (or empty string for a
            # truly-blank diff row). Strip the leading space so the
            # rendered body lines up with the add / del bodies.
            body = line[1:] if line.startswith(" ") else line
            entries.append({"kind": "context", "raw": line, "body": body})
    return entries


def _assign_line_numbers(entries: list[dict[str, Any]]) -> None:
    """Populate ``line_num`` on each entry using CC's row-counter rule.

    Mirrors CC's ``numberDiffLines`` (``Fallback.tsx:423``): a single
    counter starts at ``1`` and increments on every context / add row.
    Remove rows carry the *next* add / context row's number (so the
    paired +/- pair visually share a number) — we follow CC's behaviour
    exactly by incrementing the counter on remove rows *after* assigning
    them the pre-increment value. This produces e.g.::

        5   ctx
        5 - old
        5 + new
        6   ctx

    which is what CC renders.
    """
    counter = 1
    for entry in entries:
        kind = entry["kind"]
        if kind in ("hunk", "marker"):
            entry["line_num"] = None
            continue
        if kind == "del":
            entry["line_num"] = counter
            counter += 1
        elif kind in ("add", "context"):
            entry["line_num"] = counter
            counter += 1
        else:
            entry["line_num"] = None


def _compute_gutter_width(entries: list[dict[str, Any]]) -> int:
    """Return the character width the line-number column needs.

    CC's ``maxWidth`` is ``maxLineNumber.toString().length + 1``. We
    use the same ``+1`` so the gutter always has at least one trailing
    space and the sigil has room to breathe. Returns ``0`` when there
    are no numbered rows (so the renderer can skip the gutter entirely).
    """
    max_num = 0
    for entry in entries:
        n = entry.get("line_num")
        if isinstance(n, int) and n > max_num:
            max_num = n
    if max_num <= 0:
        return 0
    return len(str(max_num)) + 1


def _pair_inline_highlights(
    entries: list[dict[str, Any]],
    *,
    enabled: bool,
) -> None:
    """Attach ``inline_parts`` to paired add / del entries.

    Walks the body entries and groups adjacent ``del`` + ``add`` blocks.
    For each ``(del, add)`` pair runs :func:`_word_diff_parts` on their
    bodies; when the change ratio is below threshold both entries get
    their respective inline token lists:

    * The ``del`` entry's ``inline_parts`` marks *removed* tokens
      (changes from the old line's perspective).
    * The ``add`` entry's ``inline_parts`` marks *added* tokens.

    ``inline_parts`` is set to ``None`` when:

    * ``enabled`` is ``False`` (caller didn't opt in).
    * The pair was rejected by the change-ratio threshold.
    * The entry is a context / hunk / marker row (never paired).

    The renderer treats ``None`` as "fall back to whole-line colour".
    """
    if not enabled:
        for entry in entries:
            entry["inline_parts"] = None
        return

    # Initialize all entries to "no inline highlight" so unpaired rows
    # fall back to whole-line colouring.
    for entry in entries:
        entry["inline_parts"] = None

    i = 0
    n = len(entries)
    while i < n:
        entry = entries[i]
        if entry["kind"] != "del":
            i += 1
            continue
        # Collect a run of consecutive del rows.
        del_run_start = i
        while i < n and entries[i]["kind"] == "del":
            i += 1
        del_run_end = i
        # Collect the following run of consecutive add rows.
        add_run_start = i
        while i < n and entries[i]["kind"] == "add":
            i += 1
        add_run_end = i
        del_run = entries[del_run_start:del_run_end]
        add_run = entries[add_run_start:add_run_end]
        if not del_run or not add_run:
            continue
        # Pair them up one-to-one; excess rows on either side keep
        # ``inline_parts = None``. Mirrors CC's ``pairCount = min(...)``
        # behaviour.
        pair_count = min(len(del_run), len(add_run))
        for k in range(pair_count):
            del_entry = del_run[k]
            add_entry = add_run[k]
            # ``_word_diff_parts`` returns parts for the *new* line.
            # We run it once and derive the old-line view from the
            # inverse direction so the two rows agree on which tokens
            # changed.
            new_result = _word_diff_parts(
                del_entry["body"], add_entry["body"]
            )
            old_result = _word_diff_parts(
                add_entry["body"], del_entry["body"]
            )
            if new_result is None or old_result is None:
                continue
            add_entry["inline_parts"] = new_result[0]
            del_entry["inline_parts"] = old_result[0]


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
    line_numbers: bool = False,
    inline_highlight: bool = False,
    full_width_bg: bool = False,
    inline_add_color: str = "greenBright",
    inline_del_color: str = "redBright",
    show_markers: bool = True,
    indent: str = "",
    first_row_prefix: str = "",
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
    if not show_markers:
        # 07-20-tool-message-rendering-polish: skip ``---`` / ``+++``
        # file-marker lines AND ``@@ ... @@`` hunk headers entirely.
        # Mirrors Claude Code's ``StructuredDiff`` (``FileEditToolDiff``
        # never renders the raw markers — only the body rows). We keep
        # the body lines (+/-/space) so the diff content stays intact.
        diff_lines = [
            ln for ln in diff_lines
            if not ln.startswith("---")
            and not ln.startswith("+++")
            and not ln.startswith("@@")
        ]

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

    # CC-alignment pre-processing. The classification + numbering +
    # pairing passes are cheap (single linear scan each); we run them
    # unconditionally so the renderer can branch on entry metadata
    # instead of recomputing per row. When no CC features are enabled
    # the gutter width is 0, the inline_parts dict key is set to None,
    # and the renderer falls back to :func:`_render_diff_line`.
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries)
    _pair_inline_highlights(entries, enabled=inline_highlight)
    gutter_width = _compute_gutter_width(entries) if line_numbers else 0
    cc_mode = bool(
        line_numbers or inline_highlight or (full_width_bg and (add_bg_color or del_bg_color))
    )

    # ``first_row_prefix`` (parent ``⎿`` gutter alignment): when set,
    # the first body row carries the prefix instead of ``indent`` so
    # the parent's ``⎿`` glyph sits on the same line as the first body
    # row (CC ``MessageResponse`` pattern). Once we've consumed it on
    # the first body row, subsequent rows fall back to ``indent``.
    first_row_pending = bool(first_row_prefix)

    # Body: one Element per diff line.
    for entry in entries:
        kind = entry["kind"]
        raw = entry["raw"]
        if kind == "hunk":
            elements.append(Text(raw, color=hunk_color, bold=True))
            continue
        if kind == "marker":
            elements.append(Text(raw, dimColor=True))
            continue

        body = entry["body"]
        if kind == "add":
            color = add_color
            bg = add_bg_color
            sigil = "+"
            inline_color = inline_add_color
        elif kind == "del":
            color = del_color
            bg = del_bg_color
            sigil = "-"
            inline_color = inline_del_color
        else:  # context
            color = context_color or ""
            bg = None
            sigil = " "
            inline_color = ""

        # First body row consumes the parent ``⎿`` gutter prefix; all
        # subsequent rows fall back to the continuation ``indent``.
        row_prefix = first_row_prefix if first_row_pending else ""
        first_row_pending = False

        if cc_mode:
            elements.append(
                _render_diff_row_cc(
                    body=body,
                    sigil=sigil,
                    base_color=color,
                    inline_parts=entry.get("inline_parts"),
                    inline_color=inline_color,
                    bg_color=bg,
                    full_width_bg=full_width_bg,
                    line_num=entry.get("line_num"),
                    gutter_width=gutter_width,
                    indent=indent,
                    row_prefix=row_prefix,
                )
            )
            continue

        # Legacy fast path — no CC features requested. Reuses the
        # pre-CC renderer so byte-for-byte output stays identical.
        # When ``indent`` is set (or this is the first body row and
        # ``first_row_prefix`` is set) we wrap the row in a prefixed
        # Box so the body lands under a parent gutter (Jarvis's
        # archived Edit row pattern).
        prefix = row_prefix if row_prefix else indent
        if kind == "add" or kind == "del":
            line_el = _render_diff_line(
                raw,
                color=color,
                language=language,
                prefix=sigil,
                use_highlight=use_highlight,
                theme=highlight_theme,
                bg_color=bg,
            )
            if prefix:
                elements.append(
                    Box(Text(prefix), line_el, flexDirection="row")
                )
            else:
                elements.append(line_el)
        else:
            # Context line (leading space) or empty line — inherit the
            # terminal default unless ``context_color`` is set. Empty
            # diff lines (``""``) come from blank source rows; we still
            # emit a Text so the row count matches the diff.
            ctx_el = Text(raw, color=context_color)
            if prefix:
                elements.append(
                    Box(Text(prefix), ctx_el, flexDirection="row")
                )
            else:
                elements.append(ctx_el)

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
    line_numbers: bool = props.get("line_numbers", False)
    inline_highlight: bool = props.get("inline_highlight", False)
    full_width_bg: bool = props.get("full_width_bg", False)
    inline_add_color: str = props.get("inline_add_color", "greenBright")
    inline_del_color: str = props.get("inline_del_color", "redBright")
    show_markers: bool = props.get("show_markers", True)
    indent: str = props.get("indent", "")
    first_row_prefix: str = props.get("first_row_prefix", "")
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
            line_numbers=line_numbers,
            inline_highlight=inline_highlight,
            full_width_bg=full_width_bg,
            inline_add_color=inline_add_color,
            inline_del_color=inline_del_color,
            show_markers=show_markers,
            indent=indent,
            first_row_prefix=first_row_prefix,
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
    line_numbers: bool = False,
    inline_highlight: bool = False,
    full_width_bg: bool = False,
    inline_add_color: str = "greenBright",
    inline_del_color: str = "redBright",
    show_markers: bool = True,
    indent: str = "",
    first_row_prefix: str = "",
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
    line_numbers:
        When ``True``, prefix each body row with a CC-style line-number
        gutter ``<padded_num><sigil>`` (e.g. ``" 5+"``). The number is
        a row counter that increments on every context / add / del row
        — mirrors CC's ``numberDiffLines`` (``Fallback.tsx:423``).
        Defaults to ``False`` for backward compatibility. Has no effect
        on hunk / file-marker rows.
    inline_highlight:
        When ``True``, pair adjacent ``-`` / ``+`` lines and run
        :class:`difflib.SequenceMatcher` on their whitespace tokens.
        Changed tokens are rendered in the brighter
        ``inline_add_color`` / ``inline_del_color``; unchanged tokens
        inherit the row's diff colour. Lines whose change ratio exceeds
        CC's ``0.4`` threshold fall back to whole-line colouring.
        Defaults to ``False`` for backward compatibility.
    full_width_bg:
        When ``True`` (and ``add_bg_color`` / ``del_bg_color`` is set),
        set ``flushBackgroundToWidth=True`` on the outer Box of each
        add / del row so the per-row colour band spans edge-to-edge
        across the terminal width. Defaults to ``False`` for backward
        compatibility.
    inline_add_color:
        Brighter colour applied to changed tokens on ``+`` rows when
        ``inline_highlight=True``. Defaults to ``"greenBright"`` (CC's
        ``diffAddedWord`` semantics).
    inline_del_color:
        Brighter colour applied to changed tokens on ``-`` rows when
        ``inline_highlight=True``. Defaults to ``"redBright"`` (CC's
        ``diffRemovedWord`` semantics).
    show_markers:
        When ``True`` (default), render ``---`` / ``+++`` file-marker
        lines and ``@@ ... @@`` hunk headers from the underlying
        :func:`difflib.unified_diff` output. When ``False``, skip both
        — keeping only the ``+`` / ``-`` / context body rows. This
        mirrors Claude Code's ``StructuredDiff`` (``FileEditToolDiff``
        never surfaces the raw markers). Defaults to ``True`` for
        backward compatibility; downstream callers that want CC-parity
        pass ``show_header=False, show_markers=False`` together.
    indent:
        Optional literal string prepended to every continuation body row
        (default ``""``). Used by callers that embed the diff under a
        parent glyph (e.g. Jarvis's archived Edit row: ``⎿`` on the
        first visual line + matching indent on every continuation row
        so the diff lines up under the gutter). Hunk and marker rows
        are not prefixed (they're already filtered out when
        ``show_markers`` is ``False``); the indent only applies to
        ``+`` / ``-`` / context body rows. When ``first_row_prefix`` is
        also set, the first body row uses ``first_row_prefix`` instead
        — see below.
    first_row_prefix:
        Optional literal string prepended to the *first* body row in
        lieu of ``indent`` (default ``""``). Used by callers that want
        the parent's ``⎿`` glyph on the same visual line as the first
        body row (CC's ``MessageResponse`` pattern): pass
        ``first_row_prefix="  ⎿  "`` and the first row carries it;
        continuation rows still use ``indent`` so all rows line up
        under the glyph's column. Empty (default) preserves the
        legacy behaviour where every body row (including the first)
        uses ``indent``.
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

    CC-aligned (all new props on)::

        StructuredDiff(
            before, after,
            language="python",
            line_numbers=True,
            inline_highlight=True,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
        )
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
            line_numbers=line_numbers,
            inline_highlight=inline_highlight,
            full_width_bg=full_width_bg,
            inline_add_color=inline_add_color,
            inline_del_color=inline_del_color,
            show_markers=show_markers,
            indent=indent,
            first_row_prefix=first_row_prefix,
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
        line_numbers=line_numbers,
        inline_highlight=inline_highlight,
        full_width_bg=full_width_bg,
        inline_add_color=inline_add_color,
        inline_del_color=inline_del_color,
        show_markers=show_markers,
        indent=indent,
        first_row_prefix=first_row_prefix,
        box_props=box_props,
    )
