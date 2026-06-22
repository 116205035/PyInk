# PyInk Architecture

This document explains the design of PyInk at the level a contributor
needs in order to change the framework confidently. It is not an API
tour (see [api-reference.md](./api-reference.md)) nor a getting-started
guide (see the [README](../README.md)).

## Overview

PyInk is a Python port of the design philosophy behind
[`ink`](https://github.com/vadimdemedes/ink) (TypeScript), re-grounded
on **signals** instead of React hooks. Where ink says "function
components rerun on every render and hooks happen to be how state
survives those reruns", PyInk says "function components run exactly
once on mount and state lives in signals that the render loop
subscribes to".

That single decision cascades through every subsystem:

| Subsystem | ink (React) | PyInk (signals) |
|---|---|---|
| Component body | Re-runs on every state change | Runs once on mount |
| State primitive | `useState` / `useReducer` | `signal(initial)` |
| Side effects | `useEffect` with deps array | `effect(fn, deps=...)` |
| Memoisation | `useMemo` / `useCallback` | `computed(fn)` |
| Refs | `useRef` (stable mutable box) | `ref(initial)` (non-reactive) |
| Reconciliation | Diff old vs new Element tree | Mount + unmount only |
| Children | JSX `<Box><Text/></Box>` | Positional: `Box(Text("a"))` |

The goal of the port is *not* line-for-line fidelity with ink. It is
to keep the **ergonomics** of writing a TUI the way you would write a
React/SolidJS component, while embracing Python's threading model
instead of borrowing JavaScript's event loop.

The rest of this document walks through each subsystem and explains
*why* it looks the way it does.

## Reactive Model

Five primitives live in `pyink.core.signal`:

- **`signal(initial)`** — a writable, observable cell. Read via
  `.value`, write via `.value = x`. Reading inside an `effect` /
  `computed` body auto-subscribes the caller.
- **`computed(fn)`** — lazily-evaluated derived value. The function
  runs on the first `.value` read and is cached until one of the
  signals it read changes. Throws `CyclicDependency` if it detects a
  cycle through itself.
- **`effect(fn, deps=None)`** — side-effect that re-runs on dependency
  changes. `fn` may return a cleanup callable. Returns a dispose
  callable. `deps` controls re-run granularity (see below).
- **`ref(initial)`** — non-reactive mutable box. Use it for timer
  handles, raw-mode flags, and other state that must survive re-renders
  without participating in reactivity.
- **`batch(fn)`** — coalesce multiple signal writes into a single
  notification. Nested batches only flush when the outermost one exits.

### Why signals, not hooks

React's hooks model is built around a specific invariant: **a component
function can be called many times, and hooks need to remember state
across those calls**. That invariant forces a `rules-of-hooks` lint
pass (no conditional hooks, no loops of hooks, top-level only) and an
`order-of-hooks` reconciliation protocol (hooks must be called in the
same order every render).

Signals don't need that. A `Signal` object is just a mutable cell with
a subscriber set — it exists independent of any render pass. You can
store one in a local variable, a closure, a dict, or as an attribute on
any object. The only thing that ties a signal to a component is the
`effect` that *reads* it.

So PyInk's simplification is: **component bodies run exactly once**.
State is allocated up front (typically via `signal(...)` at the top of
the component body). The render loop subscribes to whatever signals the
layout pass reads, and re-paints when they change. The component body
never re-runs.

### Two-phase flush

A reactive system has to deal with the diamond dependency problem:

```
       signal s
       /      \
   computed a   computed b
       \      /
        effect e
```

If `s` is written, both `a` and `b` recompute, and `e` would be
notified twice — once via `a`, once via `b`. Worse, if `e` is notified
*before* `b` has recomputed, `e` observes a stale `b`.

PyInk solves this with a two-phase flush:

1. **Phase 1 — fire every subscriber.** Signals fire their direct
   subscribers (which include both `Computed` and `Effect` objects).
   `Computed._on_source_changed` recomputes eagerly and notifies its
   own subscribers, cascading synchronously. `Effect._on_dependency_changed`
   *defers* itself onto a queue rather than re-running inline.

2. **Phase 2 — drain the deferred effects.** After every Computed has
   refreshed its cache, we re-run the deferred effects. Each effect
   re-evaluates its deps; if they actually changed, it re-runs its body.

A monotonic `notification_epoch` counter lets an effect collapse
redundant triggers within the same flush — if both `a` and `b` notify
`e` during the same epoch, `e` defers itself once and runs once.

### Thread safety

PyInk is fully synchronous and uses threads for concurrency (no
`asyncio`). Every `Signal` / `Computed` / `Effect` owns its own
`threading.RLock`. The lock guards the compare/assign/snapshot sequence
in the setter so that a TOCTOU race can't cause notifications to fire
twice (or be skipped). Notifications always happen *outside* the lock
to avoid re-entrant writes deadlocking.

Cross-signal state (`_current_observer`, `_batch_depth`, `_notifying`,
`_notification_epoch`, `_deferred_effects`) lives in
`contextvars.ContextVar`s and module-level locks. The `ContextVar`
choice means a worker thread does *not* inherit an in-progress mount's
observer / batch state — it starts with a clean slate.

### `deps` semantics

`effect(fn, deps=...)` supports three modes:

- `deps=None` — auto-track every signal / computed read inside `fn`.
  The set of subscriptions is rebuilt on every run.
- `deps=[]` — mount effect. Runs exactly once on mount; cleanup fires
  on dispose.
- `deps=[sig_a, sig_b, ...]` — re-run only when any dep's `.value`
  changes (`!=`). Pass the Signal / Computed object itself, not its
  current value.

Auto-tracking is the default because it matches the common case ("re-run
when anything I read changes"). Explicit deps exist for when you want
to read a signal *without* subscribing — e.g. logging the current value
inside a periodic tick.

## Reconciler

The reconciler (`pyink.core.reconciler`) turns an `Element` tree into
an `Instance` tree. It exposes two operations:

- `Reconciler.mount(element, parent=None) -> Instance | None`
- `Reconciler.unmount(instance) -> None`

That's it. There is no diffing, no in-place update, no prop patching.
"Replacing" a tree is `unmount(old) + mount(new)` at the call site
(see `Instance.rerender`).

### Element vs Instance

- **`Element`** — an immutable description of a node. Produced by
  `create_element(type, *children, **props)`. `type` is either a string
  (`"box"`, `"text"`, `"provider"`) for host elements, or a callable for
  function components.
- **`Instance`** — the mutable runtime node. Two concrete kinds:
  - `HostInstance` — materialised from a host element. Holds the
    original element + a list of child instances (for containers) or a
    list of raw text leaves (for `"text"` hosts).
  - `ComponentInstance` — materialised from a function component. Owns
    the closure created by invoking the component function once, plus
    the list of effect-dispose callables registered inside the body.

### Mount walk

```
mount(element, parent):
    if element.type is a string:
        return _mount_host(element, parent)
    if element.type is callable:
        return _mount_component(element, parent)
```

`_mount_host` is straightforward:

1. Allocate a `HostInstance`.
2. If the tag is `"provider"`, push `(ctx_id, value)` onto the context
   stack *before* mounting children (see [Context
   stack](#context-stack)).
3. For each child `Element`, recurse via `mount(child, self)`.
4. If a child is a bare `str` / `Callable[[], str]`, auto-wrap it as a
   `"text"` host (only `Text` formally accepts string children, but
   the reconciler tolerates strays so pseudo-hosts in tests don't need
   a `Text` wrapper everywhere).
5. If the tag is `"provider"`, pop the stack in a `finally` block so a
   child raising doesn't corrupt sibling subtrees.

`_mount_component` is more interesting:

1. Allocate a `ComponentInstance`.
2. Bind the instance as `_current_component` via a `ContextVar` token.
   This is how `effect(...)` calls inside the component body auto-bind
   their dispose to the instance (see [Effect auto-dispose](#effect-auto-dispose)).
3. Invoke `element.type(**element.props)` exactly once.
4. Restore the `_current_component` token in a `finally`.
5. Recursively mount whatever the component returned (Element /
   tuple / list / str / None).

### Unmount walk

```
unmount(instance):
    if instance is None: return
    for child in instance.children (post-order):
        if isinstance(child, (HostInstance, ComponentInstance)):
            unmount(child)
    instance.unmount()
```

`ComponentInstance.unmount` invokes every registered effect dispose in
reverse registration order. This is why callers don't need to manually
manage effect cleanup — the reconciler does it for them.

### Effect auto-dispose

`effect(...)` consults `_current_component` at creation time. If a
component instance is bound, the effect's dispose callable is registered
with that instance via its `_on_effect_created` hook. On unmount, the
reconciler invokes every registered dispose, which cascades to the
effect's own cleanup.

This means the pattern:

```python
def MyComponent():
    count = signal(0)

    def _setup():
        timer = start_timer(lambda: count.value + 1)
        return timer.cancel

    effect(_setup)  # auto-disposed on unmount
    return Text(lambda: f"count = {count.value}")
```

…just works. The caller never holds a dispose handle; the framework
remembers.

### Context stack

The Context system (`pyink.core.context`) backs `use_context`,
`Provider`, `use_focus`, and `use_focus_manager`. It is a flat
`ContextVar` holding a list of `(ctx_id, value)` pairs.

The key invariant: **the stack only lives for the duration of a
subtree's mount**. When a `"provider"` host mounts, its value is
pushed *before* children mount. When the subtree finishes mounting,
the value is popped — even if an exception was raised.

This is correct *because* component bodies run once at mount. A
component reads its context via `use_context(ctx)` during mount; it
never re-reads later. So the stack doesn't need to outlive the mount
traversal.

To make a Provider's value reactive, pass a `Signal` (or any callable)
as the value and have the consumer read it inside an effect / callable
`Text` leaf. The Signal's own reactivity drives the update; the
Provider just hands the Signal reference down.

## Layout Engine

The layout engine is a pure-Python flexbox subset. It does not depend
on Yoga, `node-flexbox`, or any native code. The trade-off is
documented below.

### Pipeline

1. **`build_flex_tree(host_instance) -> FlexNode`** — walks the
   reconciler's `Instance` tree and produces a flat `FlexNode` tree.
   Function-component instances are skipped (only host nodes survive).
   `"provider"` hosts collapse to a fragment of their children.
   `_pyink_static=True` boxes (sentinels emitted by `Static`) return
   `None` so they never participate in layout.
2. **`layout_root(flex_root, columns, rows) -> LayoutNode`** — runs the
   recursive `_layout_node` pass, applies root margin, and produces
   the post-layout `LayoutNode` tree consumed by the renderer.
3. **`render_layout_to_string(layout_root) -> str`** — walks the
   `LayoutNode` tree, paints text/backgrounds/borders into a 2D grid,
   and joins the grid into a string.

### What's in the flex subset

Supported on `Box`:

- `flexDirection`: `row` / `column` / `row-reverse` / `column-reverse`
- `justifyContent`: `flex-start` / `center` / `flex-end` /
  `space-between` / `space-around` / `space-evenly`
- `alignItems` / `alignSelf`: `flex-start` / `center` / `flex-end` /
  `stretch` / `baseline` (baseline is approximated as top-align)
- `alignContent` (accepted but only partially honoured)
- `flexWrap`: `nowrap` / `wrap` / `wrap-reverse` (best-effort; complex
  wrap cases are marked xfail)
- `padding` / `paddingX` / `paddingY` / `paddingTop` / `paddingRight` /
  `paddingBottom` / `paddingLeft` (and the matching `margin*` family)
- `width` / `height` / `minWidth` / `maxWidth` / `minHeight` /
  `maxHeight`
- `gap` / `columnGap` / `rowGap`
- `flexGrow` / `flexShrink` / `flexBasis`
- `borderStyle` (single / double / round / bold + per-side visibility
  flags + per-side colour overrides)

Deliberately **out of scope**: percentages, `aspectRatio`, absolute
positioning, `order`. These either raise or are silently ignored.

### Three passes per container

For each container, layout does:

1. **Measure** — recursively lay out every child under an "at-most"
   constraint to learn its natural size.
2. **Distribute** — grow (positive free space, weighted by
   `flexGrow`) or shrink (negative free space, weighted by
   `flexShrink × current_size`) along the main axis.
3. **Position** — apply `justifyContent` on the main axis,
   `alignItems` / `alignSelf` on the cross axis.

Cross-axis `stretch` triggers a re-layout pass so the child fills the
perpendicular dimension.

### min-content floor

A subtle but critical guard: every `FlexNode` carries a
`min_content_main` field. For text leaves this is `1` (one row / one
column). For containers it's the aggregate of children plus padding /
gaps along the main axis.

Shrink distribution clamps each child at its `min_content_main`. This
prevents the classic "0-height row that still paints content → sibling
overlap" bug, where a text leaf is compressed to height 0 by shrink,
then paints its line anyway because the renderer's `height > 0` guard
fails.

### CSS `maxHeight` / `maxWidth` semantics

A set `max*` is an **upper bound on the content-driven size**, not a
fill target. So when `width` / `height` are unset but `maxWidth` /
`maxHeight` are set, the engine switches to fit-content mode and clamps
the resolved size afterwards. This is what lets a scrollable text
viewport `maxHeight=10` show fewer rows than its content without
forcing 10 rows when content is short.

### Text measurement

`string_width(text)` returns the display width of a string as it would
occupy cells in a fixed-width terminal:

- CJK characters count as 2 (via `wcwidth`).
- Combining marks count as 0.
- ANSI escape sequences (CSI and OSC) count as 0 — they pass through
  to the wrapped output unchanged.
- Everything else counts as 1.

`wrap_text(text, max_width, mode=...)` splits a paragraph into
display-width-bounded lines. `mode` controls trailing behaviour:
`"wrap"` (soft-wrap), `"hard"` (break long words), `"truncate"` /
`"truncate-start"` / `"truncate-middle"` / `"truncate-end"` (replace
overflow with nothing / leading ellipsis / mid ellipsis / trailing
ellipsis).

### clip-to-content-box

The painter (`render_layout._paint_node`) walks the layout tree and
writes into a 2D `_Grid` keyed by absolute coordinates. The grid's
bounds match the root's `width × height`; anything that would land
outside is silently dropped. This is the safety net that keeps
oversized content from corrupting neighbouring rows when the flex
engine can't fully resolve a constraint conflict.

### Callable / Signal style props

Every layout and decoration prop accepts `T | Callable[[], T] | Signal[T]`.
The unwrap happens inside `_resolve(prop)` at layout time. When the
layout pass runs inside the render-loop effect's tracking context,
signal reads establish subscriptions — so writing to a signal drives a
re-paint automatically.

This is what powers reactive backgrounds, reactive borders, and
reactive `scroll_offset` without a separate "reactive prop" API.

## Render Pipeline

The render pipeline (`pyink.render.pipeline` + `instance` + `diff`)
mounts the tree, runs the render loop, and writes ANSI to stdout.

### Inline mode is the default

PyInk's default render mode is **inline**: the live frame is painted
into the normal scrollback, never erasing the user's prior output.
This matches the Claude Code / `ink --inline` UX. Alternate-screen
(fullscreen) mode is opt-in via `render(tree, alternate_screen=True)`.

The single rule that keeps inline mode safe: **never emit `\x1b[2J`**
(full-screen clear). Frame updates use `cursor-up + \x1b[2K + rewrite`
sequences that touch only the rows which actually changed.

### Frame diff

`write_diff(old_frame, new_frame, stdout)` walks the two frames
line-by-line. On the first paint (`old_frame is None`) the new frame is
written verbatim followed by a `cursor-up` sequence that parks the
cursor at the top-left of the painted region. On subsequent paints,
each changed row gets `\r\x1b[2K` + the new content; the cursor is
moved down / up as needed and finally parked back at row 0.

The implementation is intentionally simple — line-by-line comparison,
no Myers diff. Ink itself uses the same approach in its MVP, and our
output rows are the layout engine's grid rows, so there's no real
"insertion in the middle" scenario unless the tree itself changes
shape.

### Alternate screen

`Terminal.enter_alternate_screen` emits `\x1b 7` (DECSC — save cursor)
+ `\x1b[?1049h` (swap to alternate buffer) + `\x1b[?25l` (hide cursor).
The matching exit emits `\x1b[?25h` + `\x1b[?1049l` + `\x1b 8`
(DECRC — restore cursor). The explicit save/restore is redundant on
conformant terminals but covers holdouts (older `cmd.exe`, some
embedded terminals) whose `1049` implementation forgets to save the
cursor.

### FPS throttle

A `_FpsThrottle` coalesces a burst of `schedule` calls into one paint
per `1/max_fps` seconds. A daemon thread sleeps on a
`threading.Event`; `schedule` sets the event, the thread runs the
latest callback after waiting out the remaining interval. If multiple
callbacks arrive within the same window only the last one runs
(callers all paint the same tree, so this is correct).

Idle behaviour: when nothing is pending the loop parks on
`_wakeup.wait()` *without* a timeout. This matters — earlier revisions
computed `wait_for` based on `last = 0.0`, which made `wait_for`
negative on idle frames and degenerated into a busy spin that pinned a
CPU core at 100% on static frames.

### TTY clamp

`render(tree, rows=N)` honours the caller-supplied viewport *unless*
the caller overspecifies it relative to the real terminal. If stdout
is a TTY and `rows=30` is passed but the terminal only has 20 lines,
the viewport is clamped down to 20. Without this, the painted frame
overflowed, the terminal scrolled, and the next repaint's cursor-up
math landed in the scrollback — corrupting every subsequent frame.

The clamp only fires when stdout is a real TTY. Captured streams
(`io.StringIO` in tests, piped output) cannot scroll, so the caller's
explicit viewport is trustworthy.

### Bracketed paste

Raw-mode entry emits DECSET 2004 (`\x1b[?2004h`) so the terminal wraps
paste payloads in `\x1b[200~ ... \x1b[201~` markers. The key loop's
`_dispatch_sequences` accumulates everything between the markers into
a buffer and flushes it as a single `Key(paste=...)` event. Handlers
see the whole paste as one edit (one `on_change`) instead of N.

Terminals that don't implement the mode silently ignore the escape,
so single-char events keep working as the fallback.

### UTF-8 incremental decoder

Multi-byte UTF-8 sequences (CJK characters, emoji) can be split across
two `os.read` / `msvcrt.getch` chunks. The Terminal wraps every raw
chunk in `codecs.getincrementaldecoder("utf-8")(errors="replace")` so
a partial lead byte is buffered internally and only emitted to the key
parser once the trailing continuation bytes arrive. Without this, a
single `你` keystroke (`\xe4\xbd\xa0`) surfaced as three junk `Key`
events.

### Windows console codepage

On a non-UTF-8 Windows locale (e.g. zh-CN with codepage 936 / GBK) the
IME emits GBK bytes for CJK characters; the UTF-8 decoder then converts
every illegal lead byte into `U+FFFD`. Raw-mode entry therefore flips
the console input + output codepage to 65001 (UTF-8) for the duration
of raw mode and restores the user's locale codepage on exit. This is
process-local and doesn't touch the system locale.

### SIGINT handling

`exit_on_ctrl_c=True` (the default) installs a process-wide SIGINT
handler that calls `Instance.unmount` on every active instance. In raw
mode Ctrl+C arrives as byte `\x03` instead of raising SIGINT, so the
pipeline *also* subscribes a Ctrl+C listener via `Terminal.on_key` and
maps `Key(ctrl=True, input="c")` to unmount.

## Hooks

Hooks are the bridge between component bodies and the outside world.
They must be called from inside a function-component body mounted via
`render` — they consult the active-Instance `ContextVar` populated by
the pipeline. `measure_element` is the lone exception (imperative,
side-effect-free, callable from any thread).

| Hook | Purpose |
|---|---|
| `use_input(handler, *, is_active=True)` | Subscribe `handler(key: Key)` to keyboard events. Auto-disposed on unmount. |
| `use_app()` | Return `AppHandle` with `exit(code)` and `wait_until_render_flush()`. |
| `use_window_size()` | Return `WindowSize(columns, rows)` snapshot. |
| `use_interval(callback, interval_ms, *, is_active=True)` | Periodically invoke `callback` on a daemon thread. |
| `use_context(ctx)` | Read the nearest Provider's value for `ctx`. |
| `use_focus(options=None) -> FocusHandle` | Subscribe the calling component to the nearest focus manager. |
| `use_focus_manager() -> FocusManagerHandle` | Create a focus manager for the current subtree. |
| `use_box_metrics(ref) -> Computed[BoxMetrics]` | Subscribe to measurements of the element `ref` points at. |

### Factory + Impl pattern

Most externals (`Spinner`, `TextInput`, `SelectInput`, `ConfirmInput`,
`StreamingText` with `reveal_speed > 0`, `Markdown` with a reactive
source) are **factories returning an Element whose `type` is a function
component**. The factory itself never runs hooks — only the wrapped
function does, when the reconciler mounts it.

This pattern lets callers write:

```python
Box(
    Spinner(type="dots"),
    TextInput(on_submit=...),
    Text("..."),
)
```

…outside of a render context. The factory just builds an `Element`;
the hooks (`use_input`, `use_interval`) run later, when the reconciler
mounts that element inside a live `Instance`.

### `is_active` evaluation timing

`use_input(handler, is_active=...)` evaluates `is_active` on every
keypress, not on mount. This lets a `Signal[bool]` (or any callable)
drive focus toggling without re-subscribing the handler. The handler
closure captures the signal in a `Ref` and reads `.value` *without*
subscribing, so a focus change doesn't trigger a re-render — it just
gates the next dispatch.

### Context-based focus

`use_focus_manager` creates a `FocusManager` and exposes it via a
module-level Context. `use_focus` reads the nearest manager from
context (falling back to a `NullFocusManager` when no
`use_focus_manager` subtree wraps the consumer) and registers a
`FocusHandle` on mount. The handle's `is_focused` is a `Signal[bool]`,
so consumers read `.value` inside a callable style prop / `Text` leaf
to subscribe to focus changes.

Tab / Shift+Tab default binding is intentionally not wired here — the
hooks expose `focus_next` / `focus_previous` on the manager handle and
let callers hook them up to `use_input` in their own key handler. This
keeps the focus hooks free of any input-parsing dependency.

## Externals Pattern

"Externals" (`pyink.externals`) are opt-in components that carry heavier
dependencies or non-essential surface area. They are imported
explicitly:

```python
from pyink.externals import Spinner, Markdown
```

…and are **not** re-exported from the top-level `pyink` namespace.

### Lazy import + optional-dependencies

Each external that needs an extra dependency lazy-imports it inside
the function body:

```python
def HighlightedCode(code, *, language="text", ...):
    try:
        from pygments import lex
    except ImportError as exc:
        raise ImportError(
            "HighlightedCode requires pygments. "
            "Install with: pip install pyink[highlight]"
        ) from exc
    ...
```

This means `pip install pyink` gets you the core; `pip install
pyink[highlight]` adds Pygments; `pip install pyink[markdown]` adds
`markdown-it-py`; `pip install pyink[all]` gets both. The optional
group only matters when the component is actually used.

### Factory + Impl split

The factory validates props, captures them in a closure, and returns
`create_element(_Impl, **captured_props)`. The Impl is a function
component that runs hooks (`use_input`, `use_interval`) and returns
the rendered tree. This split exists because:

- Factories should be callable outside a render context (you can build
  a `Spinner(...)` element and pass it around before mounting it).
- Hooks must be called inside a component body, so they live in the
  Impl function.
- The closure captures props at factory-call time so the Impl doesn't
  need to re-read them on every render (which is good, since the Impl
  only runs once anyway).

## Thread Model

PyInk is fully synchronous; concurrency is handled by daemon threads.

### Thread inventory

| Thread | Started by | Lifetime | Purpose |
|---|---|---|---|
| Main thread | Caller | Until `Instance.wait_until_exit()` returns | Runs `render()` and blocks on the exit event |
| `pyink-fps-throttle` | `_FpsThrottle.__init__` | Until `throttle.stop()` | Coalesces signal bursts into paints |
| `pyink-input-reader` | `Terminal.enable_input` | Until last `on_key` subscriber unsubscribes | Reads stdin, parses keys, dispatches callbacks |
| `pyink-interval-N` | `use_interval` | Until dispose / unmount | Ticks `callback` every `interval_ms` |
| `pyink-resize-poll` | `Terminal.on_resize` (Windows / non-TTY) | Until last `on_resize` callback unsubscribes | Polls terminal size every 200ms |

All threads are daemons, so they die with the process if the caller
forgets to `unmount()`.

### Cross-thread signalling

Signals are safe to write from any thread. Each `Signal` /
`Computed` / `Effect` owns its own `RLock`; the setter takes the lock
for the compare/assign/snapshot sequence and releases it before
notifying subscribers. Notifications therefore never deadlock on
re-entrant writes.

The render loop's `effect` body runs on whichever thread wrote the
signal that triggered the flush. This is usually the input-reader
thread (for keystroke-driven updates) or an interval worker (for
timer-driven updates). The actual `_paint_now` runs via the
`_FpsThrottle`'s daemon thread, which serialises concurrent
schedule calls into a single paint queue.

### Exit handoff

When `Ctrl+C` arrives in raw mode, the input-reader thread calls
`inst.unmount()`. `unmount` takes the Instance lock, flips
`_unmounted = True`, tears down the tree, and sets the exit event.
The main thread's `wait_until_exit()` then returns.

The Instance lock is an `RLock` so re-entrant calls from the same
thread (e.g. `unmount` triggering an `on_exit` callback that calls
`unmount` again) are safe.

## File layout

```
src/pyink/
    __init__.py            # public API re-exports
    core/
        signal.py          # signals engine
        element.py         # Element + create_element
        component.py       # HostInstance / ComponentInstance
        reconciler.py      # mount / unmount
        context.py         # Context / Provider / context stack
        scheduler.py       # (reserved; not currently wired)
    components/
        box.py             # Box host factory
        text.py            # Text host factory
        newline.py         # Newline helper
        spacer.py          # Spacer helper
        static.py          # Static (permanent output region)
        transform.py       # Transform (string rewrite)
    hooks/
        input.py           # use_input
        app.py             # use_app
        window_size.py     # use_window_size
        interval.py        # use_interval
        context.py         # use_context
        focus.py           # use_focus / use_focus_manager
        box_metrics.py     # use_box_metrics / measure_element
        _runtime.py        # active-Instance ContextVar
        _focus_runtime.py  # FocusManager / FocusHandle runtime
        _box_metrics_runtime.py  # BoxMetrics + layout epoch signal
    layout/
        flex.py            # flex engine (FlexNode / LayoutNode)
        measure.py         # string_width / wrap_text
        render_layout.py   # paint LayoutNode -> string
        _text_width_context.py  # ContextVar for deferred text renderers
    render/
        pipeline.py        # render() entry point
        instance.py        # Instance + _FpsThrottle
        diff.py            # inline frame diff
        terminal.py        # cross-platform terminal abstraction
        ansi.py            # SGR / borderchar helpers
        keys.py            # Key dataclass + parse_key
        key_parser.py      # byte-stream -> key sequence parser
    externals/
        spinner.py         # Spinner
        link.py            # OSC 8 hyperlink
        divider.py         # Divider
        streaming_text.py  # StreamingText
        highlighted_code.py  # HighlightedCode (pygments)
        markdown.py        # Markdown (markdown-it-py)
        diff.py            # StructuredDiff (difflib)
        text_input.py      # TextInput
        select_input.py    # SelectInput
        confirm_input.py   # ConfirmInput
```

## Testing strategy

PyInk ships ~1200 tests (`tests/`), organised alongside the source
tree. The test renderer `render_to_string(tree, columns=N)` is the
workhorse: it mounts the tree, runs one layout pass, paints, and
returns the string — no threads, no stdout, no FPS throttle.

This lets unit tests assert against the painted output directly:

```python
def test_counter_renders_initial_value():
    tree = Box(Text("count = 0"))
    assert render_to_string(tree, columns=20) == "count = 0"
```

Integration tests that need the live pipeline drive `render` with
synthetic stdin / stdout (`io.StringIO`) and step the clock manually.

## What's next

The [design-decisions](./design-decisions.md) document catalogues the
specific trade-offs (signals vs hooks, pure-Python flex vs Yoga, inline
default vs alternate screen, etc.) in ADR-lite format. The
[api-reference](./api-reference.md) lists every exported name with its
signature.
