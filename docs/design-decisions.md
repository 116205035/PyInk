# PyInk Design Decisions

This document collects the architectural decisions that shape PyInk's
public API. Each entry follows an ADR-lite format: **Context** (why we
had to decide), **Decision** (what we decided), **Consequences** (what
that costs and what it buys).

The list is curated — only decisions that affect how a user writes
PyInk code are included. Implementation-level choices ("which lock to
use", "how to name an internal helper") live in the source-tree
docstrings.

---

## 1. Signals, not React hooks

**Context**: ink is built on React, so it inherits the hooks model:
`useState`, `useEffect`, `useMemo`. Hooks work because React reruns
the component function on every state change, and the hook call order
gives React a way to remember which state belongs to which hook.

**Decision**: PyInk uses signals (`signal`, `computed`, `effect`,
`ref`, `batch`). Function-component bodies run **exactly once on
mount**; state lives in `Signal` objects; the render loop subscribes
to whatever signals the layout pass reads and re-paints when they
change.

**Consequences**:

- *Pros*: No rules-of-hooks lint pass. Signals are first-class values —
  store them in dicts, pass them to functions, read them in any
  thread. Reactive derivations compose naturally (`computed` of
  `computed`).
- *Cons*: Conceptual model is different from React. Users coming from
  ink need to relearn where state lives. "Re-render" no longer means
  "rerun the function"; it means "flush pending signal writes".

---

## 2. Function components only

**Context**: React supports both class components and function
components. ink uses function components exclusively, but the option
exists.

**Decision**: PyInk supports function components only. A component is
any callable that takes keyword arguments (props) and returns an
`Element` / tuple / str / None.

**Consequences**:

- *Pros*: One mental model. No `this`, no `render()` method, no
  lifecycle method ordering. Hooks always live at the top of a
  function body.
- *Cons*: Callers who want class-based inheritance patterns have to
  roll their own composition.

---

## 3. Children as positional args

**Context**: ink uses JSX: `<Box><Text>A</Text><Text>B</Text></Box>`.
Python has no JSX equivalent; f-strings and DSLs don't compose well
with type checkers.

**Decision**: Children are positional arguments. `Box(Text("a"),
Text("b"), padding=1)`. Strings are only allowed inside `Text` —
`Box("hello")` auto-wraps via the reconciler, but the type checker
won't catch it.

**Consequences**:

- *Pros*: Reads like Python. Type-checker-friendly (mypy sees the
  element constructors as plain functions). Fragments work via
  tuple/list unpacking (`Box(*[Text(x) for x in items])`).
- *Cons*: Less visually distinct than JSX. No equivalent of JSX's
  `key=` warning for list reconciliation (PyInk doesn't reconcile, so
  it doesn't matter).

---

## 4. Inline mode by default; alternate screen opt-in

**Context**: Two TUI UX patterns coexist in the wild. **Fullscreen**
(Textual, `curses`-style) swaps to an alternate screen buffer, takes
over the whole terminal, and restores everything on exit. **Inline**
(Claude Code, `ink`'s default) paints into the normal scrollback,
preserving prior output.

**Decision**: `render()` defaults to inline mode. Alternate screen is
opt-in via `render(tree, alternate_screen=True)`.

**Consequences**:

- *Pros*: Default behaviour preserves user scrollback. Frame updates
  use cursor-up + line-clear (no `\x1b[2J`), so the painted region
  composes naturally with ordinary stdout. Apps that want fullscreen
  flip one flag.
- *Cons*: Inline mode requires careful cursor tracking; bugs show up
  as "garbled borders on a short terminal" when the painted frame
  overflows. The TTY clamp (see Decision 11) defends against this.

---

## 5. Pure-Python flex, not Yoga

**Context**: ink binds Yoga (C++) via `yoga-layout-prebuilt`. A native
binding gives accurate flex behaviour for free but adds a binary
dependency and a build step.

**Decision**: PyInk ships a pure-Python flex subset. No native code;
the only runtime dependency is `wcwidth` for CJK / wide-character
measurement.

**Consequences**:

- *Pros*: `pip install pyink` works on every Python 3.11+ platform
  with no compiler toolchain. The engine is ~1600 LOC; a contributor
  can hold it in their head.
- *Cons*: The subset is intentionally limited. Percentages,
  `aspectRatio`, absolute positioning, `order`, and complex wrap
  cases are out of scope. Some flex edge cases (per-child
  rounding-off-by-one in equal shrink) are marked xfail.

---

## 6. Python 3.11+ required

**Context**: PyInk leans on modern Python features: `from __future__
import annotations`, `match` statements, `dataclass(slots=True)`,
`TypeVar` defaults, PEP 604 unions in runtime contexts.

**Decision**: Require Python 3.11 or newer.

**Consequences**:

- *Pros*: Cleaner type annotations. `Literal` / `Protocol` /
  `runtime_checkable` work without deprecation shims. `slots=True`
  dataclasses save memory.
- *Cons*: Users on 3.10 or older can't install PyInk. The cut is
  deliberate — supporting 3.10 would cost more than it gains.

---

## 7. Fully synchronous; threads for concurrency

**Context**: Python has two viable concurrency models — `asyncio`
(single-threaded event loop) and threads (OS-level preemption).
Terminal I/O is naturally blocking, and the dominant use case
(streaming AI token output, polling background tasks, reading
keystrokes) maps cleanly to threads.

**Decision**: PyInk is fully synchronous. Concurrency is handled by
application-level daemon threads. There is no event loop, no
`await`, no `async def` component functions.

**Consequences**:

- *Pros*: No async colouring problem — every function is callable
  from every thread. Signals are safe to write from any thread (each
  `Signal` owns its own `RLock`). Hooks like `use_interval` just
  spawn a daemon thread and let it tick.
- *Cons*: Threads are heavier than coroutines. A PyInk app with many
  spinners / pollers will spawn many daemon threads. In practice this
  is fine — daemons are cheap, and the framework cleans them up on
  unmount.

---

## 8. Two-phase flush (Computed before Effect)

**Context**: In a reactive graph, the diamond dependency problem
appears immediately. If `effect E` reads both `signal S` and
`computed C` (where `C` is derived from `S`), writing `S` notifies
both `C` and `E`. If `E` re-runs before `C` has recomputed, `E`
observes a stale `C`.

**Decision**: Notification flush is two-phase. Phase 1 fires every
subscriber synchronously; `Computed` recomputes eagerly and cascades,
but `Effect` defers itself onto a queue. Phase 2 drains the queue
after every upstream `Computed` has refreshed.

**Consequences**:

- *Pros*: Effects always observe a consistent snapshot. No need for
  the caller to batch writes manually or worry about subscription
  order.
- *Cons*: A tiny latency cost — deferred effects re-run on the
  next loop iteration of the flush, not inline. In practice this is
  invisible (microseconds).

---

## 9. Context stack: push at subtree mount, pop at subtree unmount

**Context**: React's Context uses a tree-walk: a Provider only affects
its descendants in the Instance tree, and `useContext` traverses
parent links at read time. PyInk needs the same semantics.

**Decision**: A flat `ContextVar` holds a list of `(ctx_id, value)`
pairs. Provider mount pushes `(ctx_id, value)`; the reconciler pops
it in a `finally` after the Provider's subtree finishes mounting.
`use_context(ctx)` scans the list top-down for the first matching id.

**Consequences**:

- *Pros*: Correct because component bodies run once at mount. The
  stack only needs to live for the duration of the mount traversal —
  no need to maintain parent-context links on every Instance. No
  async safety issues (`ContextVar` is async-safe by construction).
- *Cons*: If a Provider's `value` changes without re-mounting the
  consumer, the consumer doesn't see the new value. This matches
  signals-model semantics; to make a Provider's value reactive, pass
  a `Signal` as the value and have the consumer read it inside an
  effect.

---

## 10. `measure_element` ref pattern

**Context**: ink exposes `measureElement(ref)` which reads the layout
metrics of a rendered element. The caller passes a `ref` to the
element and then calls `measureElement(ref)` to read its current
`width` / `height` / `left` / `top`.

**Decision**: PyInk mirrors this exactly. `Box(ref=my_ref)` has its
`ref.value` back-filled with a `LayoutNode` by the layout pass.
`measure_element(ref)` reads the current snapshot imperatively;
`use_box_metrics(ref)` returns a `Computed[BoxMetrics]` that refreshes
after every layout pass.

**Consequences**:

- *Pros*: Symmetric with ink's API — ink users feel at home.
  Imperative and reactive reads share the same ref object.
- *Cons*: The ref's value is mutated by the framework. Callers must
  not hold on to a stale `LayoutNode` reference — re-read after
  every paint if you need a fresh snapshot.

---

## 11. `render()` TTY clamp

**Context**: If the caller passes `rows=30` to `render()` but the
real terminal only has 20 lines, the painted frame overflows the
screen. The terminal scrolls, and the next repaint's cursor-up math
(assumes every painted row is still on-screen) lands on the wrong
rows — corrupting every subsequent frame. This manifested as
"garbled borders on a short terminal".

**Decision**: When stdout is a real TTY and the caller-supplied
dimension exceeds the actual terminal size, the dimension is clamped
down to the actual size. Non-TTY streams (CI, `io.StringIO` in tests,
piped output) honour the caller's explicit value unchanged — they
can't scroll, so the bug can't fire.

**Consequences**:

- *Pros*: Inline mode is robust against caller misjudgment of the
  terminal size. The bug class ("short terminal garbles everything")
  is gone.
- *Cons*: A caller who genuinely wants a viewport smaller than the
  real terminal still gets it. A caller who tries to force a larger
  viewport silently gets clamped — the contract is "we never overflow
  the screen".

---

## 12. CSS `maxHeight` / `maxWidth` semantics (not a fill target)

**Context**: A natural way to constrain a scrollable text viewport is
`Box(maxHeight=10)`. But CSS `max-height` is an upper bound on the
content-driven size, not a fill target — `max-height: 10` doesn't
force the box to 10 rows when content is short.

**Decision**: A set `max*` is an upper bound. When `width` / `height`
are unset but the corresponding `max*` is set, the engine switches to
fit-content mode and clamps the resolved size afterwards.

**Consequences**:

- *Pros*: Matches CSS / Yoga behaviour. Callers who want an exact
  size still have `width` / `height`. `Text.scroll_offset` works
  predictably because the resolved height reflects actual content.
- *Cons*: Callers expecting "fill to max" semantics are surprised.
  The fix is `height=N` instead of `maxHeight=N`.

---

## 13. Style / layout props accept `T | Callable[[], T] | Signal[T]`

**Context**: ink lets you write `<Text color={() => isActive() ? "green" : "gray"}>`.
PyInk needs the same capability — reactive colours, reactive borders,
reactive `scroll_offset` — but with a Pythonic spelling.

**Decision**: Every layout-affecting and decoration prop is evaluated
through a `_resolve(prop)` helper at layout time. The helper unwraps
`Signal[T]` (reads `.value`), invokes `Callable[[], T]`, and passes
plain `T` through unchanged.

**Consequences**:

- *Pros*: One uniform "reactive prop" mechanism. Writing to a signal
  inside a callable prop's body auto-subscribes the render loop (the
  layout pass runs inside the render-loop effect's tracking context).
  No `useMemo` / `useCallback` analogue needed.
- *Cons*: Callers must be aware that callable props are evaluated on
  every layout pass, not cached. Heavy work inside a callable prop
  will hurt throughput.

---

## 14. `use_input(is_active=...)` evaluates per keypress

**Context**: Focus management between multiple inputs needs a way to
gate which input receives keystrokes. Re-subscribing on every focus
change would be wasteful and race-prone.

**Decision**: `is_active` accepts a `bool`, a `Signal[bool]`, or a
0-arg callable returning `bool`. The handler closure captures it in
a `Ref` and reads `.value` *without* subscribing on every keypress.
A focus change therefore doesn't trigger a re-render — it just gates
the next dispatch.

**Consequences**:

- *Pros*: Focus toggling is cheap. The same `use_input` subscription
  stays live for the component's whole mount; only the gating flag
  changes.
- *Cons*: The `Signal` write that flips `is_active` does **not**
  propagate to the handler — the handler reads `.value` imperatively.
  This is intentional (keystroke dispatch must stay imperative), but
  it's a wrinkle users need to learn.

---

## 15. Externals: lazy import + optional-dependencies

**Context**: Phase 3 added content-rendering externals (`Markdown`,
`HighlightedCode`, `StructuredDiff`) that pull in heavy third-party
libraries (`markdown-it-py`, `pygments`). Forcing every user to
install those would bloat the dependency tree.

**Decision**: Externals are opt-in via `from pyink.externals import
Markdown`. Each external that needs an extra lazy-imports it inside
the function body and raises `ImportError("pip install pyink[...]")`
on first call without the extra installed.

**Consequences**:

- *Pros*: `pip install pyink` stays minimal (just `wcwidth`). Users
  opt into heavier deps via extras: `pip install pyink[highlight]`,
  `pip install pyink[markdown]`, `pip install pyink[all]`.
- *Cons*: Users hitting the `ImportError` need to read the message.
  The message is prescriptive ("pip install pyink[highlight]"), so
  this is usually a one-step fix.

---

## 16. `externals` not in top-level namespace

**Context**: Even with lazy imports, having `Markdown` / `Spinner` /
`TextInput` in the top-level `pyink` namespace would encourage users
to import them by default, defeating the optional-dependency split.

**Decision**: Externals live in `pyink.externals` and are **not**
re-exported from `pyink.__init__`. Users must write `from
pyink.externals import Spinner`.

**Consequences**:

- *Pros*: The top-level namespace is lean. IDE autocomplete on
  `pyink.` shows the core building blocks; heavier externals only
  appear when the user opts in.
- *Cons*: Two import paths to remember. The README and API reference
  call this out explicitly.

---

## 17. `Static` is append-only (no removal)

**Context**: ink's `<Static>` renders a list of items above the live
frame, once, and never touches them again. Removing earlier items
doesn't erase already-written output — that's just how terminals work
(once you've written to scrollback, it's gone).

**Decision**: PyInk's `Static(items, render_item)` mirrors this.
Reactive sources (`Signal[list]` / `Callable[[], list]`) flush newly
appended items incrementally; items that disappeared or moved are
**not** re-rendered or erased.

**Consequences**:

- *Pros*: Matches ink semantics. No surprises for users porting from
  ink. Scrollback behaves like ordinary stdout.
- *Cons*: Users expecting "diff the list and update" semantics are
  surprised. The fix is to manage the live region separately from
  `Static`.

---

## 18. No `VirtualList` / windowed rendering

**Context**: Long lists (1000+ items) need windowed rendering to avoid
painting every row. ink-shipped libraries like `ink-table` don't
window; users who need windowing compose their own slice + offset.

**Decision**: PyInk does not ship a `VirtualList` component. Callers
who need windowing slice their data and render only the visible
window. `Text.scroll_offset` covers the "scroll within a fixed-height
Box" case.

**Consequences**:

- *Pros*: The framework stays small. Callers retain full control of
  the windowing strategy (key bindings, page size, cursor animation).
- *Cons*: No out-of-the-box infinite list. The fix is a small wrapper
  component on the caller side; examples/ shows the pattern.

---

## 19. `Text.scroll_offset` as a public prop

**Context**: The multi-line `TextInput` cursor-follow viewport needs a
way to "scroll" a multi-line text payload inside a fixed-height Box
without exposing internal hooks. The same capability is useful for
application code (log viewers, paginated content).

**Decision**: `Text(scroll_offset=N)` is a public prop. When the text
has more lines than the layout's granted height, a `height`-tall
window slides down by `N` lines so a later portion becomes visible.
Accepts `int | Signal[int] | Callable[[], int] | None`.

**Consequences**:

- *Pros*: One public mechanism for "scroll text inside a box" — used
  by `TextInput` internally and available to application code.
  Reactive forms (Signal / callable) plug into the render loop.
- *Cons*: The prop is only meaningful when the text's content
  overflows the granted height; under-overflow it's a no-op. Callers
  need to compute the right offset themselves (e.g. based on cursor
  position).

---

## 20. `render_to_string` for tests; `render` for live

**Context**: Tests need a side-effect-free way to assert against the
painted output. Live apps need the full pipeline (threads, stdout,
FPS throttle). Mixing the two leads to tests that spawn threads or
real TTYs.

**Decision**: Two entry points:

- `render_to_string(tree, columns=80) -> str` — sync, no threads, no
  stdout. Mounts, lays out, paints, returns the string. Used by
  ~1200 tests.
- `render(tree, ...) -> Instance` — full live pipeline. Mounts,
  starts the render-loop effect, spawns the FPS throttle / input
  reader / interval workers, registers SIGINT / atexit hooks.

**Consequences**:

- *Pros*: Tests are deterministic and fast. The live pipeline is
  opt-in. `render_to_string` is also useful for one-shot snapshots
  in non-TUI scripts.
- *Cons*: Two code paths to keep in sync. In practice the layout
  engine is shared; only the outer driver differs.

---

## 21. `use_focus` + `use_focus_manager` (Context-based, no Tab default)

**Context**: Focus management in TUIs needs (a) a way for a component
to say "I want focus" and (b) a way to rotate focus between siblings.
ink has `useFocus()` and `useFocusManager()`. Tab / Shift+Tab default
binding is debatable.

**Decision**: PyInk mirrors ink's split. `use_focus_manager()` creates
a manager and exposes it via a module-level Context. `use_focus()`
registers the calling component with the nearest manager. Tab /
Shift+Tab default binding is **not** wired — the manager exposes
`focus_next` / `focus_previous` and lets the caller hook them up to
`use_input` in their own key handler.

**Consequences**:

- *Pros*: Focus hooks have no input-parsing dependency. Callers can
  bind any key (Tab, `j`/`k`, digit keys) to focus rotation. Works
  equally well for `use_focus_manager`-rooted trees and for callers
  using `use_input` directly.
- *Cons*: Out of the box, Tab does nothing. Callers need 5 lines of
  glue (`use_input` + `focus_next` / `focus_previous`). Examples/
  shows the pattern.

---

## 22. `use_interval` is a thin daemon thread, not a scheduler

**Context**: `useInterval` in React is built on `setTimeout` chains.
Python has no event loop by default; threads are the natural
concurrency primitive.

**Decision**: `use_interval(callback, interval_ms)` spawns a daemon
thread that sleeps `interval_ms` between invocations. The thread stops
via `threading.Event`; `dispose` sets the event and joins with a 1s
timeout.

**Consequences**:

- *Pros*: Callbacks that write to signals are safe (per-signal
  `RLock`). The interval lifecycle matches the component's mount —
  unmount tears the thread down automatically.
- *Cons*: 10 spinners is 10 daemon threads. Lightweight in practice,
  but a different cost profile from React's setTimeout chains.

---

Each decision is reflected in the relevant module's docstring. For the
full rationale on any single decision, the source is authoritative;
this document is the summary.
