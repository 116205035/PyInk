# PyInk API Reference

This document lists every name exported from the `pyink` package and
the `pyink.externals` subpackage. For the architectural *why* behind
each API, see [architecture.md](./architecture.md); for the trade-offs
that shaped each signature, see [design-decisions.md](./design-decisions.md).

## Import paths

```python
import pyink                        # core API
from pyink import Box, Text, render, signal, use_input, ...
from pyink.externals import Spinner, Markdown, TextInput, ...
```

Externals are **not** re-exported from the top-level `pyink` namespace
(Decision 16). Import them explicitly from `pyink.externals`.

## Built-in Components

### `Box(*children, **props)`

Flex container host element. Mirrors ink's `<Box>`.

**Layout props** (all accept `T | Callable[[], T] | Signal[T]`; see
Decision 13):

| Prop | Type | Default | Notes |
|---|---|---|---|
| `flexDirection` | `"row"` / `"column"` / `"row-reverse"` / `"column-reverse"` | `"row"` | Main-axis direction. |
| `justifyContent` | `"flex-start"` / `"center"` / `"flex-end"` / `"space-between"` / `"space-around"` / `"space-evenly"` | `"flex-start"` | Main-axis alignment. |
| `alignItems` | `"flex-start"` / `"center"` / `"flex-end"` / `"stretch"` / `"baseline"` | `"stretch"` | Cross-axis alignment. `baseline` is approximated as top-align. |
| `alignSelf` | same as `alignItems` + `"auto"` | `"auto"` | Override this child's cross-axis alignment. |
| `alignContent` | same as `justifyContent` + `"stretch"` | `"flex-start"` | Partially honoured. |
| `flexWrap` | `"nowrap"` / `"wrap"` / `"wrap-reverse"` | `"nowrap"` | Best-effort; complex wrap cases are xfail. |
| `padding` / `paddingX` / `paddingY` / `paddingTop` / `paddingRight` / `paddingBottom` / `paddingLeft` | `int` | `0` | Interior spacing. |
| `margin` / `marginX` / `marginY` / `marginTop` / `marginRight` / `marginBottom` / `marginLeft` | `int` | `0` | Exterior spacing. |
| `width` / `height` | `int` | `None` | Fixed size. |
| `minWidth` / `maxWidth` / `minHeight` / `maxHeight` | `int` | `None` | CSS semantics — `max*` is an upper bound, not a fill target. |
| `gap` / `columnGap` / `rowGap` | `int` | `0` | Spacing between siblings. |
| `flexGrow` | `float` | `0.0` | Grow weight. |
| `flexShrink` | `float` | `1.0` | Shrink weight. |
| `flexBasis` | `int` | `None` ("auto") | Initial main-axis size. |

**Decoration props**:

| Prop | Type | Notes |
|---|---|---|
| `borderStyle` | `"single"` / `"double"` / `"round"` / `"bold"` / `dict[str, str]` | 8-key dict overrides per-character mapping. |
| `borderColor` / `borderTopColor` / `borderRightColor` / `borderBottomColor` / `borderLeftColor` | colour spec | Per-side border colour. |
| `border...BackgroundColor` | colour spec | Per-side border background. |
| `border...DimColor` | colour spec | Per-side dim border colour. |
| `borderTop` / `borderRight` / `borderBottom` / `borderLeft` | `bool` | Per-side visibility (default `True` when `borderStyle` is set). |
| `backgroundColor` | colour spec | Fills the box interior. |
| `ref` | `Ref[LayoutNode \| None]` | Back-filled by the layout pass; read via `measure_element` / `use_box_metrics`. |

**Colour specs** accept `"red"` / `"gray"` / etc., `"#ff0000"`,
`"rgb(255,0,0)"`, or `"ansi256(9)"`.

**Children**: positional. Must be `Element` instances; stray `str` /
`Callable[[], str]` children are auto-wrapped as a `"text"` host.

### `Text(*children, **props)`

Text leaf host element. The only component that accepts raw `str` /
`Callable[[], str]` children directly.

**Props** (all style props accept `T | Callable[[], T] | Signal[T]`):

| Prop | Type | Default | Notes |
|---|---|---|---|
| `color` | colour spec | `None` | Foreground colour. |
| `backgroundColor` | colour spec | `None` | Background colour. |
| `bold` / `italic` / `underline` / `strikethrough` / `inverse` / `dimColor` | `bool` | `False` | SGR style toggles. |
| `wrap` | `"wrap"` / `"hard"` / `"truncate"` / `"truncate-start"` / `"truncate-middle"` / `"truncate-end"` | `"wrap"` | Word-wrap behaviour when content overflows width. |
| `scroll_offset` | `int \| Signal[int] \| Callable[[], int] \| None` | `None` | Slides a `height`-row window down by N lines so a later portion becomes visible. Phase 5. |

**Children**: `str`, `Callable[[], str]`, nested `Element` (e.g.
`Newline()`). Nested `Text` styling is dropped — PR4 does not
implement ink's nested-Text transform pipeline.

### `Newline(count=1)`

Convenience text leaf containing `count` newline characters. Must be
a child of `Text`.

### `Spacer(**props)`

Flex spacer.

- `size=N` — fixed-width box of N cells.
- Otherwise — `flexGrow=1` (consumes available main-axis space).

Any other `Box` prop is forwarded.

### `Static(items, render_item, *, style=None)`

Permanently render a list of items above the live frame. Mirrors
ink's `<Static>`.

**Parameters**:

- `items` — `list[T]` / `Signal[list[T]]` / `Callable[[], list[T]]`.
  Reactive sources flush newly-appended items incrementally.
- `render_item(item, index) -> Element` — called once per item, in
  source order, with the absolute index.

Append-only: items that disappeared or moved are not re-rendered or
erased (matches ink semantics).

### `Transform(*children, transform=fn)`

Rewrite the rendered string of children via `transform(line, idx) ->
str`. Children are laid out and rendered to a string, split into
lines, each line passed through `transform`. Typical uses:
uppercasing, ANSI gradients, indentation, hanging indents.

Constraint: `transform` should not change a line's visible width —
layout has already positioned children based on original widths.

## Externals

Import via `from pyink.externals import ...`. Externals with optional
dependencies raise `ImportError("pip install pyink[...]")` on first
call without the extra installed.

### `Spinner(*, type="dots", color=None, interval_ms=80)`

Animated loading indicator. Frame data from `SPINNERS` (mirrors
`cli-spinners`). `type` is a spinner name (`"dots"`, `"line"`, `"arc"`,
`"moon"`, `"star"`, ...); unknown names fall back to `"dots"`.
`interval_ms` defaults to `80` (cli-spinners' canonical `dots`
interval). A `Signal[int]` frame index advanced by `use_interval`;
the frame character is rendered lazily by a callable child on `Text`.

### `Link(*children, url, **text_props)`

Clickable OSC 8 terminal hyperlink. `*children` is the label content;
`url` is emitted verbatim (callers should use a parseable URI:
`https://…`, `file://…`, `mailto://…`); `**text_props` are forwarded
to the emitted `Text` leaf (`color`, `bold`, `underline`, …). Children
are rendered to a string at mount time; wrap the `Link` call site in a
parent that re-renders for reactive content.

### `Divider(*, label=None, direction="horizontal", border_style="single", color=None, width=None, height=None, padding=0)`

Visual section separator. `direction="horizontal"` produces a
single-line separator; `"vertical"` produces a column inside a row
container. `border_style` accepts `"single"` / `"double"` / `"round"`
/ `"bold"` / `dict` (unknown names fall back to `"single"`). `label`
is centred, with `padding` cells of gutter on each side. `color`
paints line + label uniformly.

### `StreamingText(buffer, *, cursor=None, cursor_color=None, reveal_speed=0, color=None, **text_props)`

Stream-in text display. `buffer` is `Signal[str]` / `Callable[[], str]`
/ `str`. `cursor` is a trailing glyph (`None` for no cursor);
`cursor_color` wraps it. `reveal_speed=0` (default) is instant;
`reveal_speed>0` enables a typing animation at that many characters
per second. `**text_props` forwarded to `Text`.

### `HighlightedCode(code, *, language="text", theme=None, line_numbers=False, **text_props)`

Pygments-driven syntax highlighting. Requires `pip install
pyink[highlight]`. `language` is a Pygments lexer name (`"text"`
skips highlighting; `"auto"` defers to `guess_lexer`). `theme` is a
`dict[str, colour_spec]` keyed on short token names
(`"String"`, `"Number"`, ...). `line_numbers=True` prepends a dim
right-aligned gutter.

### `Markdown(source, *, theme=None, **box_props)`

Render Markdown (CommonMark + tables) via `markdown-it-py`. Requires
`pip install pyink[markdown]`. `source` is `str` / `Signal[str]` /
`Callable[[], str]`. `theme` is a `dict[str, colour_spec]` for
headings, inline code, etc. Fenced code blocks render via
`HighlightedCode` when Pygments is installed, plain text otherwise.

### `StructuredDiff(before, after, *, language="text", context_lines=3, show_header=True, **box_props)`

File-edit diff display via `difflib.unified_diff`. `before` / `after`
are `str` / `Signal[str]` / `Callable[[], str]`. `language` (when not
`"text"` and Pygments is installed) activates per-line highlighting of
`+` / `-` bodies. `+` lines are green, `-` red, `@@` hunk headers
magenta.

### `TextInput(*, initial_value="", placeholder=None, on_change=None, on_submit=None, on_cursor_change=None, multiline=False, mask=None, max_length=None, color=None, cursor_color=None, cursor_style="block", is_active=True, **box_props)`

Single-line or multi-line text input. Callbacks: `on_change(value)`
fires on every edit, `on_submit(value)` on Enter,
`on_cursor_change(offset)` on every cursor move. `multiline=True`
makes Enter insert `\n`. `mask="*"` enables password masking.
`cursor_style` is `"block"` (default) / `"bar"` / `"underline"`.
`is_active` accepts `bool` / `Signal[bool]` / callable and is
evaluated per keypress. Emacs-style editing (Ctrl+A/E/K/U/W),
Shift-arrow selection, bracketed-paste are built in.

### `SelectInput(items, *, initial_index=0, on_select=None, on_change=None, multi_select=False, indicator="❯", selected_indicator="✓", unselected_indicator=" ", color=None, selected_color="green", is_active=True, **box_props)`

Keyboard-navigable option list. `items` is `list[str]` or
`list[dict]` with `label` + `value`. Navigation: ArrowUp/Down or
`j`/`k` to move, `1`..`9` to jump, Enter to confirm, Space to toggle
(multi-select only). `on_select(value)` (single) /
`on_select(list[value])` (multi) fire on Enter; `on_change(item,
index)` fires on every focused-index move.

### `ConfirmInput(on_confirm, on_cancel=None, *, prompt="Confirm?", confirm_key="y", cancel_key="n", require_enter=False, default=None, confirm_label=None, cancel_label=None, color=None, selected_color="green", is_active=True, **box_props)`

Y/N confirmation prompt. `require_enter=False` (default) fires the
callback on the keystroke; `True` highlights first, then Enter
confirms. Custom `confirm_key` / `cancel_key` supported. Esc fires
`on_cancel` when set; otherwise falls through to the surrounding
pipeline.

## Hooks

All hooks must be called from inside a function-component body mounted
via `render`. `measure_element` is the lone exception (imperative,
callable from any thread).

### `use_input(handler, *, is_active=True) -> dispose`

Subscribe `handler(key: Key)` to keyboard events.

- `handler` — called on the input-reader thread for every parsed
  keypress. Exceptions are swallowed by the dispatcher so one bad
  handler can't kill the loop.
- `is_active` — `bool` / `Signal[bool]` / 0-arg callable. Evaluated on
  every keypress. Use this for focus management (see Decision 14).
- Returns a dispose callable. Also auto-disposed on component unmount.

### `use_app() -> AppHandle`

Return an `AppHandle` for the active `Instance`.

```python
@dataclass(frozen=True)
class AppHandle:
    exit: Callable[[Any], None]               # triggers Instance.unmount
    wait_until_render_flush: Callable[[], None]
```

Both callables are safe from any thread.

### `use_window_size() -> WindowSize(columns, rows)`

Return the current terminal size as a snapshot. Re-read on every
layout pass; subscribe by reading inside a callable `Text` leaf /
`effect` / `computed`.

### `use_interval(callback, interval_ms, *, is_active=True) -> dispose`

Periodically invoke `callback` on a daemon thread.

- `callback` — zero-arg callable invoked on the worker thread.
  Exceptions are swallowed so the loop survives a bad tick.
- `interval_ms` — tick interval. Values `<= 0` start nothing.
- `is_active` — `False` at mount starts the loop paused.
- Returns a dispose callable. Auto-disposed on unmount.

### `use_context(ctx) -> T`

Read the nearest Provider's value for `ctx`. Returns `ctx.default`
when no Provider is active.

### `use_focus(options=None) -> FocusHandle`

Subscribe the calling component to the nearest focus manager.

```python
@dataclass
class FocusHandle:
    id: int
    is_focused: Signal[bool]
    is_active: bool
    focus_self: Callable[[], None]
    blur: Callable[[], None]
```

Returns a `NullFocusManager`-backed handle (always `is_focused ==
False`) when called outside any `use_focus_manager` subtree.

### `use_focus_manager() -> FocusManagerHandle`

Create a focus manager for the current subtree.

```python
@dataclass
class FocusManagerHandle:
    focus_next: Callable[[], None]
    focus_previous: Callable[[], None]
    focus: Callable[[int], None]
    enable: Callable[[], None]
    disable: Callable[[], None]
    active_id: Signal[int | None]
    wrap: Callable[..., Element]    # builds the Provider injecting the manager
```

### `use_box_metrics(ref) -> Computed[BoxMetrics]`

Subscribe to measurements of the element `ref` points at.

```python
@dataclass(frozen=True)
class BoxMetrics:
    width: int | None
    height: int | None
    left: int | None
    top: int | None
    has_measured: bool
```

Reading `.value` inside a callable `Text` leaf / `effect` / `computed`
body subscribes to layout updates.

### `measure_element(ref) -> BoxMetrics`

Imperative snapshot. Reads `ref.value` once, returns a `BoxMetrics`.
Returns `UNMEASURED` (`has_measured=False`) before the first layout
pass or after unmount. Safe to call from any thread.

Pair with the `ref=` prop on `Box` and `use_box_metrics` for reactive
updates.

## Signals

From `pyink.core.signal`. All thread-safe (each `Signal` / `Computed`
/ `Effect` owns its own `RLock`).

### `signal(initial) -> Signal[T]`

Create a writable observable cell.

- `.value` — read (auto-subscribes if caller is inside an `effect` /
  `computed` body).
- `.value = x` — write; notifies subscribers if `x != current`.

### `computed(fn) -> Computed[T]`

Lazily-evaluated derived value.

- `.value` — read. Runs `fn` on first read; caches until a dependency
  changes.
- Throws `CyclicDependency` if `fn` creates a cycle through itself.

### `effect(fn, deps=None) -> dispose`

Register a reactive side-effect.

- `fn` — zero-arg callable. May return a cleanup callable that runs
  before the next re-run and on dispose.
- `deps`:
  - `None` (default) — auto-track every signal / computed read inside
    `fn`.
  - `[]` — mount effect; runs exactly once.
  - `[sig_a, sig_b, ...]` — re-run only when any dep's `.value`
    changes (`!=`). Pass the Signal / Computed object itself, not its
    current value.
- Returns a dispose callable. Auto-disposed on component unmount when
  called from inside a function-component body.

### `ref(initial) -> Ref[T]`

Non-reactive mutable reference.

- `.value` — read (does **not** subscribe).
- `.value = x` — write (does **not** notify).

Use for timer handles, raw-mode flags, and stable mutable boxes.

### `batch(fn) -> T`

Coalesce multiple signal writes into one notification.

- `fn` — zero-arg callable. Its return value is forwarded.
- Nested `batch` calls only flush when the outermost one exits.

## Context

From `pyink.core.context`. Backs `use_context`, `use_focus`, and
`use_focus_manager`.

### `create_context(default) -> Context[T]`

Create a new Context with the given default value. Keep the returned
Context as a module-level constant — callers share one Context between
a Provider and its consumers so they agree on id.

### `Provider(ctx, value, *children)`

Host element that pushes `value` onto `ctx`'s stack for its subtree.

Push happens on mount, pop on unmount, in strict LIFO order. The
stack only lives for the duration of the mount traversal (see
Decision 9).

## Element / Component

From `pyink.core.element` + `pyink.core.component`.

### `create_element(type, *children, **props) -> Element`

Build an `Element`. `type` is a string (`"box"`, `"text"`,
`"provider"`) for host elements, or a callable for function
components. Children are positional; nested `tuple` / `list` are
flattened; `None` / `True` / `False` are filtered.

### `Element` / `ComponentInstance` / `HostInstance`

Type aliases for the runtime nodes. Callers usually don't touch these
directly — `create_element` and the component factories produce them.

## Layout

From `pyink.layout`. Useful for advanced cases (custom hosts,
measurement-only runs).

### `build_flex_tree(instance) -> FlexNode | None`

Materialise a `FlexNode` tree from a host instance tree.
Function-component instances are flattened away.

### `layout(root, *, columns=80, rows=None) -> LayoutNode`

Top-level entry point. Accepts a host instance (from the reconciler)
or a pre-built `FlexNode`. Returns the post-layout `LayoutNode` tree.

### `layout_root(flex_root, *, columns=80, rows=None) -> LayoutNode`

Lower-level entry that skips the `build_flex_tree` step.

### `render_layout_to_string(root) -> str`

Render a `LayoutNode` tree to a plain string snapshot. Used
internally by the render pipeline and by `render_to_string`.

### `string_width(text) -> int`

Display width of a string (CJK = 2, combining = 0, ANSI = 0, else 1).

### `wrap_text(text, max_width, mode="wrap") -> list[str]`

Split a paragraph into display-width-bounded lines.

### `clear_box_refs(instance) -> None`

Recursively clear `ref.value` for every Box in `instance`'s tree.
Used by `Instance.unmount` so `measure_element` returns `UNMEASURED`
after teardown.

## Render

From `pyink.render`.

### `render(tree, *, stdout=None, stdin=None, columns=None, rows=None, alternate_screen=False, exit_on_ctrl_c=True, max_fps=30) -> Instance`

Mount `tree` and start the reactive render loop.

- `tree` — root `Element`.
- `stdout` — defaults to `sys.stdout`.
- `stdin` — defaults to `sys.stdin`.
- `columns` / `rows` — fixed viewport. `None` auto-detects. Values
  that exceed the real terminal size are clamped to it when stdout is
  a TTY (see Decision 11).
- `alternate_screen` — `False` (default, inline mode) or `True`
  (fullscreen mode).
- `exit_on_ctrl_c` — treat SIGINT / raw-mode Ctrl+C as unmount.
  Default `True`.
- `max_fps` — frame-rate cap. Multiple signal writes inside one
  `1/max_fps` window collapse into a single paint.

### `render_to_string(tree, *, columns=80) -> str`

Sync, side-effect-free renderer. Mounts, lays out, paints, returns the
string. No threads, no stdout. Used by tests and CI.

### `Instance`

Live handle returned by `render`.

| Method | Purpose |
|---|---|
| `rerender(tree)` | Replace the root element tree (unmount + mount). |
| `unmount()` | Tear everything down. Idempotent. |
| `wait_until_exit()` | Block the calling thread until `unmount`. |
| `clear()` | Clear the current frame. |
| `on_exit(callback)` | Register a callback that fires on unmount. |
| `write_static(text)` | Append to the permanent output region above the frame. |
| `cleanup()` | `unmount` + remove from `atexit` registry. Safe to call multiple times. |

## Error types

### `CyclicDependency(RuntimeError)`

Raised when a `computed` function creates a cycle through itself.

## Versioning

PyInk follows semantic versioning. Pre-1.0 minor bumps may include
breaking API changes. See the [README](../README.md) for the current
status.
