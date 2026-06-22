# Migrating from Textual to PyInk

This guide is for developers who have an existing [Textual](https://textual.textualize.io/)
app and want to port it to PyInk. It maps the concepts, highlights the
ergonomic differences, and calls out the pitfalls you'll hit on the
way.

## Why migrate?

PyInk and Textual solve overlapping but different problems:

| | Textual | PyInk |
|---|---|---|
| **Reactive model** | Message bus + reactive attributes | Signals (SolidJS / Vue 3 style) |
| **Layout** | CSS-like (flexbox + grid) | Flexbox subset, props only |
| **Threading** | Internal asyncio event loop | Fully synchronous; app owns threads |
| **Default mode** | Fullscreen (alternate screen) | Inline (preserves scrollback) |
| **CSS** | Yes (full CSS dialect) | No |
| **Async** | First-class (`async def` handlers) | Sync only |
| **Widget library** | Rich built-in widget set | Minimal built-ins; externals for chat UI |

Migrate when you want inline mode (Claude Code style), synchronous app
code without asyncio colouring, a smaller dependency footprint
(Textual pulls in `rich`; PyInk's runtime dep is just `wcwidth`), or
first-class externals for chat / AI-streaming UIs (Markdown,
StreamingText, StructuredDiff).

Stay with Textual when you need CSS theming, the fullscreen panel
experience, or the built-in widget library (data tables, trees, tabbed
panels).

## Concept Mapping

| Textual | PyInk | Notes |
|---|---|---|
| `Widget` subclass | Function component | `def MyWidget(): ...` — runs once on mount |
| `App` | `render(tree)` | Returns an `Instance`; `.wait_until_exit()` blocks |
| `Reactive` attribute | `Signal` | `signal(initial)`; read `.value`, write `.value = x` |
| `on_key` / `on_click` handlers | `use_input(handler)` | Handler receives a parsed `Key` |
| `Compose` method | Component body returns elements | `return Box(Text("..."))` |
| `watch_*` methods | `effect(fn)` / `computed(fn)` | Re-runs on dependency change |
| `set_interval` / timers | `use_interval(callback, ms)` | Daemon thread; auto-disposed |
| `CSS` rules | Flex props on `Box` / `Text` | No external stylesheet |
| `Screen` / `Mode` (fullscreen) | `render(tree, alternate_screen=True)` | Opt-in |
| `DOM` tree (widget id) | `ref=Ref[LayoutNode \| None]` | Imperative: `measure_element(ref)`; reactive: `use_box_metrics(ref)` |
| `App.run()` | `render(tree).wait_until_exit()` | |
| `App.exit()` | `use_app().exit()` or `Instance.unmount()` | |
| `App.on_mount` | Component body (runs on mount) | |
| `App.on_unmount` | `Instance.on_exit(callback)` or effect cleanup | |
| `MessagePump` / message bus | Direct signal writes | No bus; signals fan out to subscribers |
| `Binding` / keymap | `use_input` + your own dispatch | See [Focus management](#focus-management) below |
| `Container` / `Vertical` / `Horizontal` | `Box(flexDirection="column"/"row")` | |
| `Label` | `Text(content)` | |
| `Input` widget | `TextInput(...)` external | Single-line + multi-line + selection + paste |
| `Static` widget | `Text(content)` or `Static(items, render_item)` | PyInk's `Static` is the *append-only log region* — different concept |
| `Button` widget | `Text("[Enter] Submit")` + `use_input` | No built-in button |
| `RadioSet` / `SelectionList` | `SelectInput(...)` external | Single + multi-select |

## Mode Choice: Inline vs Fullscreen

### Inline (default)

```python
from pyink import render

render(my_tree).wait_until_exit()
```

The live frame is painted into the normal scrollback. Frame updates use
cursor-up + line-clear (no `\x1b[2J`), so prior output is preserved.
This matches the Claude Code / `ink --inline` UX.

Choose this when:

- You want output to compose with the user's shell session.
- You're building a chat / streaming / log-tail UI.
- You want the user to be able to scroll back through prior frames.

### Fullscreen

```python
from pyink import render

render(my_tree, alternate_screen=True).wait_until_exit()
```

The terminal's alternate screen buffer is entered on mount and restored
on unmount. Cursor position is explicitly saved (DECSC) before the
buffer swap and restored (DECRC) afterwards.

Choose this when:

- You're porting a Textual app verbatim and want the same UX.
- You need full control of the viewport (e.g. a game, a dashboard).
- You don't want prior output cluttering the user's scrollback.

## Static + Scrollback

Textual's `Static` widget is a scrollable text area inside the UI. **PyInk's
`Static` is a different concept** — it's the *permanent output region
above the live frame*, mirroring ink's `<Static>`.

```python
from pyink import Box, Text, Static, render, signal

log_lines = signal([])

def App():
    return Box(
        Static(log_lines, lambda line, idx: Text(line)),
        Text(lambda: f"{len(log_lines.value)} lines total"),
        flexDirection="column",
    )
```

Items rendered via `Static` are written to stdout *above* the live
frame. Already-rendered items are never re-painted by the frame diff,
so they accumulate like ordinary log output and scroll off the top of
the viewport naturally. Append-only: removing an item from the list
does **not** erase already-written output.

This is the canonical pattern for:

- Chat message history.
- Streaming log output.
- Anything that should "scroll up" like normal stdout.

For a Textual-style scrollable text area *inside* the live frame, use
`Text(scroll_offset=N)` inside a `Box(height=K)`:

```python
from pyink import Box, Text, signal

scroll = signal(0)
content = "line\n" * 50

def LogViewer():
    return Box(
        Text(content, scroll_offset=scroll),
        height=10,
    )
```

Bind Up/Down/PageUp/PageDown via `use_input` to update `scroll.value`.

## Layout: Flexbox vs CSS Grid

Textual supports both flexbox and grid via CSS rules. **PyInk has only
flexbox** — no grid, no CSS.

| Textual (CSS) | PyInk (Box props) |
|---|---|
| `layout: horizontal` | `flexDirection="row"` |
| `layout: vertical` | `flexDirection="column"` |
| `width: 50%` | Not supported (percentages out of scope). Use `flexGrow=1` for "share space". |
| `width: 40` | `width=40` |
| `height: auto` | `height=None` (default; fit content) |
| `height: 10` | `height=10` |
| `max-height: 10` | `maxHeight=10` (CSS semantics — upper bound, not fill target) |
| `padding: 1` | `padding=1` |
| `padding: 1 2` | `paddingX=2, paddingY=1` |
| `align: center middle` | `alignItems="center", justifyContent="center"` |
| `dock: top` | No docking. Compose with `flexDirection="column"` + explicit children order. |
| `offset: 0 1` | No offset. Use margin instead. |
| Grid (`grid-size: 3 2`) | Not supported. Compose rows of `Box(flexDirection="row")` inside a `Box(flexDirection="column")`. |

### No CSS

There is no stylesheet. All styling is props on the element:

```python
# Textual
Button("Save", id="save", classes="primary")

/* app.tcss */
Button.primary {
    background: blue;
    color: white;
}
```

```python
# PyInk
Text("Save", color="white", backgroundColor="blue", bold=True)
```

Theming is done via Python constants or a theme dict passed to each
component:

```python
PRIMARY = {"color": "white", "backgroundColor": "blue", "bold": True}

Text("Save", **PRIMARY)
```

## Events and Input

### `on_key` -> `use_input`

Textual:

```python
class MyWidget(Widget):
    def on_key(self, event: events.Key) -> None:
        if event.key == "q":
            self.app.exit()
```

PyInk:

```python
from pyink import use_input, use_app
from pyink.render.keys import Key

def MyWidget():
    app = use_app()

    def on_key(key: Key):
        if key.input == "q":
            app.exit()

    use_input(on_key)
    return Text("Press q to quit")
```

The `Key` dataclass has fields: `input` (the character or name),
`ctrl` / `shift` / `meta` / `alt` (modifier flags), `up_arrow` /
`down_arrow` / `left_arrow` / `right_arrow` / `return` / `escape` /
`tab` / `backspace` / `delete` / `paste` (booleans), and `paste`
(string payload for bracketed paste events).

### Focus management

Textual's `Widget.focus()` / `App.focused` become PyInk's
`use_focus_manager()` + `use_focus()`:

```python
from pyink import use_focus_manager, use_focus, use_input, Box, Text

def App():
    manager = use_focus_manager()

    def on_key(key):
        if key.tab and not key.shift:
            manager.focus_next()
        elif key.tab and key.shift:
            manager.focus_previous()

    use_input(on_key)

    return manager.wrap(
        Box(Input1(), Input2(), flexDirection="column")
    )

def Input1():
    handle = use_focus()
    color = "green" if handle.is_focused.value else "gray"
    return Text("Input 1", color=color)

def Input2():
    handle = use_focus()
    color = "green" if handle.is_focused.value else "gray"
    return Text("Input 2", color=color)
```

There's no default Tab binding — you wire it via `use_input` so you
can use any key (Tab, `j`/`k`, digit keys).

### `on_click` / mouse

Textual has first-class mouse support. **PyInk does not handle mouse
events** (PR scope; the ink mouse API is minimal). If your Textual app
relies on mouse clicks, you'll need to replace them with keyboard
shortcuts.

## Reactive State

### `Reactive` -> `Signal`

Textual:

```python
class MyWidget(Widget):
    count = Reactive(0)

    def watch_count(self, old_value, new_value):
        self.update(f"count = {new_value}")

    def on_key(self, event):
        if event.key == "up":
            self.count += 1
```

PyInk:

```python
from pyink import signal, Text, effect

def MyWidget():
    count = signal(0)

    def _update():
        # re-runs whenever count.value changes
        pass
    effect(_update)

    # In the render closure, read count.value lazily so the
    # render loop subscribes:
    return Text(lambda: f"count = {count.value}")
```

The pattern difference:

- **Textual**: state lives on the widget; `watch_*` callbacks fire on
  change.
- **PyInk**: state lives in `Signal` objects allocated in the
  component body; `effect(...)` and callable `Text` children subscribe
  by reading `.value` inside their body.

### ` Compose` -> component body

Textual's `compose()` method yields child widgets. PyInk's component
body returns an Element tree:

```python
# Textual
class MyWidget(Widget):
    def compose(self):
        yield Label("Hello")
        yield Button("Click me")
```

```python
# PyInk
def MyWidget():
    return Box(
        Text("Hello"),
        Text("[Click me]", bold=True),
        flexDirection="column",
    )
```

### `on_mount` / `on_unmount`

Textual has lifecycle hooks on widgets. In PyInk, mount = the
component body (runs once); unmount = effect cleanup:

```python
def MyWidget():
    # "on_mount" code goes here
    print("mounting")

    def _setup():
        # subscribe to something
        return lambda: print("unmounting")  # cleanup

    effect(_setup)
    return Text("...")
```

## Threading

Textual runs an asyncio event loop inside `App.run()`. Handlers are
`async def`; long work uses `await`.

PyInk is fully synchronous. The main thread blocks in
`Instance.wait_until_exit()`; you spawn daemon threads for background
work:

```python
import threading
from pyink import signal, Text, render

count = signal(0)

def worker():
    while True:
        threading.Event().wait(1.0)
        count.value += 1

def App():
    return Text(lambda: f"ticks = {count.value}")

threading.Thread(target=worker, daemon=True).start()
render(App()).wait_until_exit()
```

### Use `use_interval` for periodic callbacks

For simple timers, prefer `use_interval` over hand-rolled threads:

```python
from pyink import use_interval, signal, Text

def Spinner():
    frame = signal(0)

    def tick():
        frame.value += 1

    use_interval(tick, 80)
    return Text(lambda: f"frame = {frame.value % 10}")
```

`use_interval` spawns a daemon thread, slices the wait into 50ms chunks
so dispose returns promptly, and auto-disposes on component unmount.

## Async / await

Textual's async handlers don't translate directly. Three options:

1. **Drop `async` / `await`** and call blocking APIs directly (e.g.
   `requests.get` instead of `httpx.AsyncClient`). Run in a daemon
   thread.
2. **Run an asyncio loop in a background thread** and bridge results
   back via signals:

   ```python
   import asyncio, threading
   from pyink import signal

   result = signal(None)

   def run_async():
       async def fetch():
           result.value = "done"
       asyncio.run(fetch())

   threading.Thread(target=run_async, daemon=True).start()
   ```

3. **Don't migrate** — keep Textual for async-heavy UIs.

Most PyInk apps end up using pattern 1 (threads + blocking APIs).

## Common Pitfalls

### 1. "My state isn't updating" / "My text isn't reactive"

The component body runs **once**. State must live in `signal(...)`,
and reads must happen lazily so the render loop can subscribe:

```python
# WRONG — plain local + eager read
def Counter():
    count = 0
    return Text(f"count = {count}")  # frozen at mount

# RIGHT — Signal + lazy read inside callable Text child
def Counter():
    count = signal(0)
    return Text(lambda: f"count = {count.value}")
```

The callable form is evaluated by the layout engine at paint time,
inside the render-loop effect's tracking context — that's what
establishes the subscription.

### 2. "Hooks called inside a factory don't work"

Externals use the **factory + Impl** pattern: the factory builds an
Element, the Impl is the function component that runs hooks. Hooks
called inside the factory body fail at mount time.

```python
# WRONG — factory is called outside a component context
def MyExternal(label):
    use_input(handler)  # RuntimeError: no active Instance
    return Text(label)

# RIGHT — factory returns an Element whose type is the Impl
def MyExternal(label):
    def _Impl():
        use_input(handler)
        return Text(label)
    return create_element(_Impl)
```

### 3. "`is_active` doesn't re-subscribe"

`use_input(handler, is_active=...)` evaluates `is_active` on every
keypress, not on mount. A `Signal[bool]` driving `is_active` doesn't
trigger a re-render — the handler just reads `.value` without
subscribing. This is intentional (Decision 14): focus toggling should
be cheap, not drive a re-render.

### 4. "Removing an item from `Static` doesn't erase it"

`Static` is append-only. Once an item is written to the permanent
output region, it's part of the scrollback and can't be erased
(terminals don't work that way). For a "live list" that supports
removal, render it inside the live frame as a `Box(flexDirection=
"column")` of `Text` children.

### 5. "My layout overflows the terminal"

If the painted frame is taller than the terminal, the terminal scrolls
and the next repaint's cursor-up math lands in the scrollback —
corrupting every subsequent frame. Defences: `render()` clamps caller-
supplied `rows=N` down to the real terminal size when stdout is a TTY;
the flex engine has a `min-content floor` (text leaves can't compress
below 1 row); the painter clips to the root content box. If your tree
genuinely doesn't fit, use `maxHeight` on scrollable regions,
`flexShrink` on non-critical boxes, or move content into `Static` so
it scrolls away naturally.

### 6. "Ctrl+C doesn't exit"

`exit_on_ctrl_c=True` is the default. If `stdin` is not a TTY (piped
input, CI), raw mode is not entered and Ctrl+C arrives as SIGINT —
the default SIGINT handler unmounts. If you disabled it
(`exit_on_ctrl_c=False`), call `use_app().exit()` from a key handler.

### 7. "My input handler runs on which thread?"

The input-reader thread (`pyink-input-reader`). Signal writes from
inside a key handler are safe (each `Signal` owns its own `RLock`).
Don't block the handler — it blocks the next keystroke. `use_app
().exit()` from inside a handler is safe.

## Migration Strategy

A practical port order:

1. **List the widgets** you use. Map each to its PyInk equivalent (see
   [Concept Mapping](#concept-mapping)). Widgets without equivalents
   need to be re-implemented as function components.
2. **Identify the data flow**. Textual's reactive attributes map to
   `Signal`s; `watch_*` methods map to `effect(...)`.
3. **Pick a mode** (inline or fullscreen). Inline is easier to debug
   (output composes with your shell).
4. **Port the root widget first.** Use `render_to_string(tree)` in
   tests to assert against the painted output without the live
   pipeline. Port child widgets one at a time.
5. **Replace async handlers** with synchronous equivalents on daemon
   threads. Bridge results back via signals.
6. **Re-skin**. Textual's CSS becomes inline props; theme via Python
   constants.

## Getting Help

- [Architecture](./architecture.md), [design decisions](./design-decisions.md),
  [API reference](./api-reference.md).
- [Examples](../examples/) — ~30 runnable demos covering every
  built-in and external.
