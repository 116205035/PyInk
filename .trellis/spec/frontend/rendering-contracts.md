# Rendering Contracts

> Cross-layer contracts for components that need layout-time measurement.

These contracts govern when a component can read the parent's granted width
and how to measure strings that carry ANSI styling. They apply to any
component whose rendered output depends on the available width (tables,
progress bars, ASCII art, gauges, …).

---

## 1. Layout Width Context: `get_current_text_width()`

### Contract

- `ink.layout._text_width_context.get_current_text_width() -> int | None`
  returns the content-box width the parent grants a `Text` leaf **during
  layout**.
- `None` means "unbounded width" (e.g. the very first subscription layout,
  or a measurement pass outside a real render).
- The context is established **only** when layout processes a
  `Text(callable)` leaf. Components that render eagerly (a factory that
  returns a `box` host element directly) **cannot** read it — the width
  context does not exist at construction time.

Source: `src/ink/layout/_text_width_context.py`, injected at
`src/ink/layout/flex.py` (Text leaf branch).

### When you need it

Any component whose rendered output depends on the available width must
defer rendering to layout time via a `Text(callable)` leaf inside a
function component. Examples:

- Tables that shrink/fit columns to available width
- Progress bars that fill a percentage of the parent width
- ASCII art that needs to know the column budget
- Any "responsive" component that changes layout with width

### Pattern

```python
from ink.components.box import Box
from ink.components.text import Text
from ink.core.element import create_element, Element
from ink.layout._text_width_context import get_current_text_width
from ink.hooks._runtime import _get_current_instance
from typing import Any


def _MyComponentImpl(**props: Any) -> Element:
    source = props["source"]

    def render_reactive() -> str:
        text = _resolve_source(source)
        if not text:
            return ""
        columns = get_current_text_width()
        if columns is None or columns < 1:
            # Fallback to viewport width when unbounded.
            inst = _get_current_instance()
            columns = 80
            if inst is not None:
                cols_attr = getattr(inst, "columns", 0)
                if isinstance(cols_attr, int) and cols_attr > 0:
                    columns = cols_attr
        return _cached_render(text, columns, props["theme"])

    return Box(Text(render_reactive), flexDirection="column")


def MyComponent(source, *, theme=None, **box_props) -> Element:
    return create_element(
        _MyComponentImpl,
        source=source,
        theme=theme or DEFAULT_THEME,
        box_props=box_props,
    )
```

### Wrong vs Correct

#### Wrong

```python
def MyComponent(source: str, **props) -> Element:
    # Eager render — layout width context is NOT available here.
    elements = render_at_fixed_width(source, columns=80)  # ← hardcoded
    return Box(*elements, flexDirection="column")
```

The component renders at a fixed width regardless of the parent's content
box. Inside a narrower parent (blockquote, bordered box, split pane) the
output overflows; inside a wider parent it underfills.

#### Correct

Defer to layout time via `Text(callable)` inside a function component —
see Pattern above.

### Common Mistake: Static source overflows in narrow parent

**Symptom**: A component renders correctly in isolation but overflows or
underfills when placed inside a narrower parent (blockquote, bordered box,
split pane).

**Cause**: The factory renders eagerly at construction time, before
layout establishes the width context. `get_current_text_width()` returns
`None`, and the component falls back to a hardcoded width.

**Fix**: Convert the factory to return a function component element whose
body returns `Text(callable)`. The callable runs at layout time, when the
width context exists.

**Trade-off**: This is a breaking change for callers that assert on the
element type — the factory now returns a function component element
(`el.type is _MyComponentImpl`) instead of a `box` host element
(`el.type == "box"`). Update tests accordingly. This trade-off is
intentional: width-aware rendering requires layout-time deferral.

### Tests Required

- Component placed in a parent narrower than viewport renders at the
  parent's width, not the viewport's.
- Component placed in an unbounded-width context falls back gracefully
  (viewport width or explicit default), does not crash.
- Nested component (inside a blockquote / list item / bordered box)
  receives a width reduced by the ancestor's indent / padding.

### Reference Consumer

`src/ink/externals/markdown.py` — the `Markdown` factory was converted
from an eager `box` host to a `_MarkdownImpl` function component in PR2
of the `pyink-markdown-render-polish` task specifically so tables could
shrink to the parent's content box.

---

## 2. ANSI String Width: `string_width()`

### Contract

- `ink.layout.measure.string_width(s: str) -> int` returns the visible
  width of `s` in terminal cells.
- It strips CSI (SGR) and OSC 8 hyperlink sequences before measuring, so
  strings carrying inline styles measure correctly.
- CJK full-width characters count as 2 cells; combining characters and
  emoji are handled via `wcwidth` (a required dependency).
- Use this **anywhere** you compute column widths, padding, or
  truncation on strings that may carry ANSI sequences.

Source: `src/ink/layout/measure.py` (`string_width` + CSI/OSC stripping
helpers).

### When to use it

- Table column width calculation: `max(widths[idx], string_width(cell))`
- Padding computation: `" " * (width - string_width(text))`
- Truncation / hardWrap budget
- Any `len(s)` on a string that came from `_render_inline` /
  `apply_style` / `_wrap_osc8` / any function that emits SGR sequences

### Wrong vs Correct

#### Wrong

```python
cell_text = _render_inline_token(inline, theme)  # carries SGR
widths[idx] = max(widths[idx], len(cell_text))   # ← inflated by CSI bytes
padding = " " * (widths[idx] - len(cell_text))   # ← too much padding
```

`len()` counts CSI bytes as visible characters, so columns become too
wide and padding misaligns cells. CJK full-width characters count as 1
in `len()` but render as 2 cells, causing further misalignment.

#### Correct

```python
from ink.layout.measure import string_width

cell_text = _render_inline_token(inline, theme)
widths[idx] = max(widths[idx], string_width(cell_text))
padding = " " * (widths[idx] - string_width(cell_text))
```

### Common Mistake: Table misaligns with styled or CJK cells

**Symptom**: Table columns misalign when cells contain `**bold**`,
`` `code` ``, or CJK characters (中文, 日本語, 한국어).

**Cause**: Width computed with `len()`, which counts ANSI CSI bytes as
visible and treats CJK full-width characters as 1 cell.

**Fix**: Use `string_width()` from `ink.layout.measure` for any width
computation on strings that may carry ANSI sequences or contain
full-width characters.

### Tests Required

- Table with styled cells (bold, inline code) aligns correctly.
- Table with CJK characters (全角 = 2 cells) aligns correctly.
- Table with mixed styled + CJK content aligns correctly.

### Reference Consumer

`src/ink/externals/markdown.py:_render_table` — column widths, cell
padding, and key-value fallback all use `string_width()` since PR2 of
the `pyink-markdown-render-polish` task.

---

## 3. Factory Component Row Composition: One Text Leaf per Source Line

### Contract

A factory component that renders tokenised / code content as a column of
rows (one row per source line) MUST emit **ONE `Text` leaf per source
line**, with per-token styling carried as **inline ANSI SGR sequences
inside that leaf's string**. It MUST NOT emit one `Text` leaf per
Pygments token (or per sigil / gutter / prefix fragment) inside a
`flexDirection="row"` Box.

This is the `<Text><Ansi>{code}</Ansi></Text>` pattern from Claude
Code's `HighlightedCode` (`Fallback.tsx:69`): a single styled leaf per
line, no per-token children.

### Why

When a row Box has multiple `Text` leaf children and the row's total
width exceeds `columns`, the flex layout invokes its **shrink
algorithm** (`src/ink/layout/flex.py`). The shrink algorithm penalises
*every flexible child* proportionally to recover the budget — and each
`Text` leaf is a flexible child. Trailing characters are silently eaten
from every leaf until the row fits.

The bug is **silent**: no error, no warning, no visual indicator. Code
just renders with missing characters:

- `print` → `pri`
- `item['code']` → `it['co'`
- `'break_high'` → `'break'`

CJK accelerates the trigger (each char is width 2, so a line reaches
`columns` faster) but is NOT the root cause — `string_width` /
`wcswidth` are correct throughout. The root cause is per-token leaves
becoming independent flex items.

With ONE `Text` leaf per source line, the entire source line is a
single flexible child. When it overflows `columns`, the layout engine's
`_measure_paragraph → wrap_text(mode="wrap")` pipeline wraps the leaf
onto subsequent visual rows (reusing the existing ANSI-aware word-break
/ hard-break logic). Continuation rows self-align under the first row's
code start column. **Zero factory-level wrap code is required.**

### Wrong vs Correct

#### Wrong — one Text leaf per token

```python
def _build_line_rows_wrong(token_rows, **kw):
    out = []
    for row_tokens in token_rows:           # row_tokens: [(type, value), ...]
        leaves = [
            Text(_token_to_ansi(v, _lookup_color(t, theme)), color=...)
            for t, v in row_tokens          # ← one leaf per token
        ]
        out.append(Box(*leaves, flexDirection="row"))
    return out
```

Each leaf is a flexible child. When the row exceeds `columns`, flex
shrink eats trailing chars from *every* leaf → silent corruption.

#### Correct — one Text leaf per source line, inline ANSI

```python
from ink.externals.highlighted_code import tokens_to_ansi_string

def _build_line_rows_correct(token_rows, theme, **kw):
    # Convert each source line's tokens to ONE ANSI-coded string.
    ansi_rows = [tokens_to_ansi_string(row, theme) for row in token_rows]
    out = []
    for ansi_str in ansi_rows:
        out.append(Box(Text(ansi_str), flexDirection="row"))  # ← single leaf
    return out
```

The entire source line is one flexible child. Overflow wraps via
`_measure_paragraph`; no chars are lost.

### Canonical tokens → ANSI converter

`src/ink/externals/highlighted_code.py:tokens_to_ansi_string` is the
canonical helper. It:

- Walks each `(token_type, value)` pair, looks up the colour via
  `_lookup_color` (most-specific Pygments token path wins), and wraps
  the value in `\x1b[<fg>m<value>\x1b[0m`.
- Splits multi-line token values (docstrings, block comments) on `\n`
  and re-emits each fragment with its own SGR span so a reset can't
  bleed across the newline onto the next row.
- Returns a single string carrying inline ANSI escapes — CC's
  `<Ansi>{code}</Ansi>` parity.

Re-use this helper from any factory that needs to render Pygments
tokens as a single leaf. `StructuredDiff` imports it directly (see
`src/ink/externals/diff.py:_compose_body_ansi`).

### Background bands on wrapped rows (StructuredDiff)

When a row also carries a background colour band that must span the
terminal width on *every* visual row of a wrapped line (CC's
`StructuredDiff` signature), the band is encoded as **in-band ANSI**
inside the same single `Text` leaf — NOT via `backgroundColor` +
`flushBackgroundToWidth` on the leaf. The reason: PyInk's bg painter
would apply bg to every cell the leaf touches, including the row's
prefix area (parent `⎿` gutter), which must keep the default terminal
bg.

Byte-layout for a wrapped row (`src/ink/externals/diff.py:1039-1105`):

```
<line 1>  <prefix><bg_open><gutter><chunk_body><pad spaces><reset>
<line 2>  <bg_open><chunk_body><pad spaces><reset>
<line 3>  <bg_open><chunk_body><pad spaces><reset>
```

Two non-obvious tricks:

1. **Trailing reset as pad-shield.** Each visual row's chunk + trailing
   pad ends with its OWN `\x1b[0m` reset. The reset byte at the very
   end of each paragraph defeats the renderer's per-row `rstrip()`: it
   is a non-whitespace byte, so the preceding pad spaces survive the
   strip and the bg band fills `target_w` on every visual row. Without
   this, wrapped rows would lose their right-side bg fill.
2. **Bg re-open per continuation row.** Each continuation row re-opens
   the bg SGR (`\x1b[48;...m`) at column 0 so the band is active from
   the start of the row, not just from the chunk's first character.

The factory pre-wraps the body via `wrap_text(body_visible,
body_chunk_w, mode="wrap")` and emits one paragraph per chunk — but
note this is a **bg-band-specific** concern. The default code-wrap case
(no bg) relies entirely on the layout engine's wrap pipeline; the
factory does not wrap.

### Who must follow this

Any factory that renders tokenised or code content as rows. Current
consumers:

- `src/ink/externals/highlighted_code.py:_build_line_rows` — one `Text`
  per source line via `tokens_to_ansi_string` (PR1 of
  `07-23-long-code-line-wrap`).
- `src/ink/externals/diff.py:_build_diff_row` / `_compose_body_ansi` /
  `_build_full_width_bg_row` — one `Text` per diff row, sigil + body
  composed into a single inline-ANSI string (PR2 of
  `07-23-long-code-line-wrap`).
- Future factories that tokenise content (logs, JSON viewers, REPL
  output, …) must follow the same pattern.

### Common Mistake: Per-token leaves silently corrupt long lines

**Symptom**: A highlighted code block or diff renders with missing
characters on long lines. CJK-heavy lines trigger it most often. No
error is raised; no warning is logged. Short lines render fine, which
hides the bug in casual testing.

**Cause**: The factory emitted one `Text` leaf per Pygments token
inside a `flexDirection="row"` Box. Each leaf became a flexible child
of the row; when the row exceeded `columns`, the flex shrink algorithm
ate trailing chars from every leaf to fit the budget.

**Fix**: Convert the per-line emission to a single `Text` leaf whose
body is the per-line ANSI-coded string produced by
`tokens_to_ansi_string`. The layout engine's wrap pipeline handles
overflow with zero character loss.

**Detection**: Add a wrap test that renders a line wider than `columns`
and asserts that tokens at the end of the line (`print`, `item['code']`,
`break_high`) appear intact in the output. A short-line-only test
suite will not catch this regression.

### Tests Required

- Long line (≥ 2× `columns`) wraps to multiple visual rows with **zero
  character loss**: assert that tokens at the line's end appear in the
  rendered output.
- Single token wider than `columns` hard-breaks (no overflow, no
  infinite loop).
- Line exactly at `columns` does not wrap spuriously.
- CJK-heavy line wraps correctly (each char width 2).
- For StructuredDiff specifically: bg band spans the full terminal
  width on every visual row of a wrapped `+` / `-` line; line-number
  gutter appears only on the first visual row.

### Reference Consumers

- `src/ink/externals/highlighted_code.py` — `_build_line_rows` emits
  one `Text` per source line; `tokens_to_ansi_string` is the canonical
  tokens → ANSI converter. Tests: `tests/externals/test_highlighted_code.py`
  (`test_long_python_line_wraps_without_char_loss` and neighbours).
- `src/ink/externals/diff.py` — `_build_diff_row` /
  `_compose_body_ansi` / `_build_full_width_bg_row` emit one `Text` per
  diff row with inline ANSI (including the bg-band-on-wrapped-rows
  trick at lines 1039-1105). Tests: `tests/externals/test_diff.py`
  (`test_long_plus_line_wraps_without_char_loss`,
  `test_full_width_bg_band_extends_across_wrapped_visual_rows`, and
  neighbours).

---

## Related

- `src/ink/layout/_text_width_context.py` — width context implementation
- `src/ink/layout/measure.py` — `string_width()` + `wrap_text()` implementation
- `src/ink/layout/flex.py` — flex shrink algorithm (the silent-corruption
  mechanism described in Section 3)
- `src/ink/externals/highlighted_code.py` — reference consumer (Sections 1+3)
- `src/ink/externals/diff.py` — reference consumer (Section 3, including
  bg-band-on-wrapped-rows)
- `src/ink/externals/markdown.py` — reference consumer (Sections 1+2)
- `.trellis/tasks/07-11-pyink-markdown-render-polish/research/` — research
  notes that established Sections 1 and 2
- `.trellis/tasks/07-23-long-code-line-wrap/prd.md` — full bug analysis +
  architectural decision that established Section 3
