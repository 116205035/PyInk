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

## Related

- `src/ink/layout/_text_width_context.py` — width context implementation
- `src/ink/layout/measure.py` — `string_width()` implementation
- `src/ink/externals/markdown.py` — reference consumer (uses both contracts)
- `.trellis/tasks/07-11-pyink-markdown-render-polish/research/` — research
  notes that established these contracts
