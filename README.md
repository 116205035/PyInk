# PyInk

A Python ink-style TUI framework built on **signals** (no React-style hooks).
Inspired by [ink](https://github.com/vadimdemedes/ink) (TypeScript),
SolidJS / Vue 3 / Preact Signals reactive model, and the
[Claude Code](https://github.com/anthropic/claude-code) terminal UX.

PyInk targets Python 3.11+, has a single runtime dependency
([`wcwidth`](https://pypi.org/project/wcwidth/) for CJK / wide-character
width), and is fully synchronous — concurrency is handled by
application-level threads.

## Status

MVP + Phase 2 + Phase 3 complete. The reactive core, layout engine,
built-in components, hooks, live render pipeline and examples all ship
in this repository. Phase 2 layers on the high-frequency Jarvis / Claude
Code TUI building blocks — animated spinners, OSC 8 hyperlinks, focusable
inputs (via a real `use_focus` / `use_focus_manager` pair backed by a
Context system), section dividers and a `measure_element` API for
dynamic, measurement-driven layout. Phase 3 adds the content-rendering
externals a Claude Code-style chat UI needs: streaming text (typing
animation), Markdown rendering, Pygments-driven syntax highlighting and
structured file diffs. See the project PRDs
(`.trellis/tasks/06-19-pyink-mvp/prd.md`,
`.trellis/tasks/06-20-pyink-phase2/prd.md`,
`.trellis/tasks/06-20-pyink-phase3/prd.md`) for the full roadmap and
design decisions.

## Install (editable)

```bash
cd D:/Projects/PyInk
pip install -e ".[dev]"
```

### Optional extras (Phase 3)

The Phase 3 content-rendering externals pull in heavier third-party
libraries. Install only what you need:

```bash
pip install pyink                     # core only (no extra deps)
pip install pyink[highlight]          # + HighlightedCode / StructuredDiff highlighting (Pygments)
pip install pyink[markdown]           # + Markdown rendering (markdown-it-py)
pip install pyink[all]                # both — full Phase 3 content surface
```

Externals are imported explicitly (`from pyink.externals import
Markdown`) — they are **not** re-exported from the top-level `pyink`
namespace, so the optional dependencies stay optional. Each external
that needs its extra raises an `ImportError` with a `pip install
pyink[...]` hint the first time it is called without the extra
installed.

## Minimal example

```python
from pyink import Box, Text, render, signal

def Counter():
    count = signal(0)

    def on_key(key):
        if key.up_arrow:
            count.value += 1

    # ``use_input`` is imported alongside the rest of the API
    from pyink import use_input
    use_input(on_key)

    return Box(
        Text(lambda: f"count = {count.value}"),
        flexDirection="column",
    )

if __name__ == "__main__":
    render(Counter(), columns=40, rows=3).wait_until_exit()
```

More examples live under [`examples/`](./examples) — see below.

## API surface

### Signals (`pyink.core.signal`)

| Name | Description |
| --- | --- |
| `signal(initial)` | Observable writable value; read with `.value`, write with `.value = x`. |
| `computed(fn)` | Lazy derived value; cached until a dependency changes. |
| `effect(fn, deps=None)` | Side-effect that re-runs on dependency changes. |
| `ref(initial)` | Non-reactive mutable reference for stable handles. |
| `batch(fn)` | Coalesce multiple signal writes into a single notification. |

`deps` semantics for `effect`:

- `deps=None` — auto-track every signal read inside `fn`.
- `deps=[]` — mount effect, runs exactly once.
- `deps=[sig_a, sig_b, ...]` — re-run only when any dep's `.value` changes
  (`!=`). Pass the Signal / Computed object itself, not its current value.

`fn` may return a cleanup callable that runs before the next re-run and on
dispose. `effect(...)` returns a dispose callable.

### Components (`pyink.components`)

The six built-in host components map 1:1 to ink's primitives:

| Name | Description |
| --- | --- |
| `Box(*children, **props)` | Flex container. Accepts layout props (`flexDirection`, `padding`, `margin`, `gap`, `width`, `height`, `flexGrow`, …) and decoration props (`borderStyle`, `borderColor`, `backgroundColor`, …). All decoration props accept `T \| Callable[[], T]` (PRD Decision 13) — callables are evaluated at render time inside the render-loop effect's tracking context. |
| `Text(*children, **props)` | Text leaf. Accepts strings / callables as children. Style props (`color`, `backgroundColor`, `bold`, `italic`, `underline`, `strikethrough`, `dimColor`, `inverse`, `wrap`) all accept `T \| Callable[[], T]` (PRD Decision 13). |
| `Newline()` | Convenience `Text("\n")`. |
| `Spacer(size=None)` | Flex spacer. With `size=N` it has a fixed width; otherwise `flexGrow=1`. |
| `Static(items, render_item)` | Permanently render a list of items above the live frame. `items` may be a plain list, a `Signal[list]`, or a `Callable[[], list]`. |
| `Transform(*children, transform=fn)` | Rewrite the rendered string of children via `transform(line, idx) -> str`. |

### Hooks (`pyink.hooks`)

All hooks must be called from inside a function-component body mounted
via `pyink.render.render`:

| Name | Description |
| --- | --- |
| `use_input(handler, *, is_active=True)` | Subscribe `handler(key: Key)` to keyboard events. Returns a dispose callable. |
| `use_app()` | Returns an `AppHandle` with `exit(code)` and `wait_until_render_flush()`. |
| `use_window_size()` | Returns the current terminal size as a `WindowSize(columns, rows)` snapshot. |
| `use_interval(callback, interval_ms, *, is_active=True)` | Periodically invoke `callback` on a daemon thread (Phase 2). Returns a dispose callable; auto-disposed on unmount. |
| `use_context(ctx)` | Read the nearest Provider's value for `ctx` (Phase 2). Returns the context's default when no Provider is mounted. |
| `use_focus(options=None) -> FocusHandle` | Subscribe the calling component to the nearest focus manager (Phase 2). Returns `{id, is_focused (Signal[bool]), is_active, focus_self(), blur()}`. |
| `use_focus_manager() -> FocusManagerHandle` | Create a focus manager for the current subtree (Phase 2). The handle exposes `focus_next` / `focus_previous` / `focus(id)` / `enable` / `disable` / `active_id` and a `wrap(*children)` that builds the Provider injecting the manager. |
| `use_box_metrics(ref) -> Computed[BoxMetrics]` | Subscribe to measurements of the element `ref` points at (Phase 2). Refreshes after every layout pass; `BoxMetrics.has_measured` is `False` until the first layout. |

### Context (`pyink.core.context`)

Phase 2 ships a Context system backed by a `ContextVar` stack. Provider
mount pushes `(id, value)`; unmount pops it. `use_context(ctx)` reads
the nearest matching entry, falling back to the context's default.

| Name | Description |
| --- | --- |
| `create_context(default) -> Context[T]` | Create a new Context with the given default value. |
| `Context[T]` | The context object — carry it around (e.g. module-level) and pass to `Provider` / `use_context`. |
| `Provider(ctx, value, *children)` | Host element that pushes `value` onto the context's stack for its subtree. |

### Externals (`pyink.externals`)

Opt-in components — import them explicitly
(`from pyink.externals import Spinner, Link, Divider`). Phase 2 +
Phase 3:

| Name | Description |
| --- | --- |
| `Spinner(*, type="dots", color=None, interval_ms=80)` | Animated loading indicator driven by `use_interval`. `type` selects a frame sequence from `SPINNERS`; unknown names fall back to `"dots"`. |
| `Link(*children, url, **text_props)` | Wrap children in an OSC 8 terminal hyperlink. Style props (`color`, `bold`, `underline`, …) are forwarded to the emitted `Text` leaf. |
| `Divider(*, label=None, direction="horizontal", border_style="single", color=None, width=None, height=None, padding=0)` | Single-line section separator, optionally carrying a centred label. Vertical mode (`direction="vertical"`) renders a column inside a row container. |
| `StreamingText(buffer, *, cursor=None, cursor_color=None, reveal_speed=0, color=None, **text_props)` | Stream-in text display. `buffer` is a `Signal[str]` / `Callable[[], str]` / `str`. `reveal_speed>0` enables a typing animation; `cursor` adds a trailing glyph. Phase 3. |
| `HighlightedCode(code, *, language="text", theme=None, line_numbers=False, **text_props)` | Pygments-driven syntax highlighting. Lazy-imports `pygments`; raises `ImportError("pip install pyink[highlight]")` on first call without the extra. Phase 3. |
| `Markdown(source, *, theme=None, **box_props)` | Render Markdown (CommonMark + tables) via `markdown-it-py`. `source` is a `str` / `Signal[str]` / `Callable[[], str]`. Fenced code blocks render via `HighlightedCode` when Pygments is installed, plain text otherwise. Raises `ImportError("pip install pyink[markdown]")` without the extra. Phase 3. |
| `StructuredDiff(before, after, *, language="text", context_lines=3, show_header=True, **box_props)` | File-edit diff via `difflib.unified_diff`. `+` green / `-` red / `@@` magenta. Optional Pygments highlighting of `+`/`-` bodies when `language != "text"` and Pygments is installed. Phase 3. |

### Imperative API (`measure_element`)

| Name | Description |
| --- | --- |
| `measure_element(ref) -> BoxMetrics` | Read the current `{width, height, left, top, has_measured}` snapshot of the element `ref` points at. Returns `UNMEASURED` (all `None`, `has_measured=False`) before the first layout pass or after unmount. Safe to call from any thread. |

Pair `measure_element` with the `ref` prop on `Box` (any
`Ref[LayoutNode | None]` passed as `ref=` is back-filled by the layout
pass) and with `use_box_metrics` for reactive updates.

### Render (`pyink.render`)

| Name | Description |
| --- | --- |
| `render(tree, *, stdout=None, stdin=None, columns=None, rows=None, alternate_screen=False, exit_on_ctrl_c=True, max_fps=30) -> Instance` | Mount `tree` and start the reactive render loop. |
| `render_to_string(tree, *, columns=80) -> str` | Sync side-effect-free renderer used by tests and CI. |
| `Instance.rerender(tree)` | Replace the root element tree. |
| `Instance.unmount()` | Tear everything down (idempotent). |
| `Instance.wait_until_exit()` | Block the calling thread until unmount. |
| `Instance.clear()` | Clear the current frame. |
| `Instance.on_exit(callback)` | Register a callback that fires on unmount. |
| `Instance.write_static(text)` | Append to the permanent output region above the frame. |

## Differences from ink (TypeScript)

PyInk is **not** a line-for-line port. The major API deltas:

| Area | ink | PyInk |
| --- | --- | --- |
| Reactivity | React hooks (`useState`, `useEffect`, `useMemo`) | Signals (`signal`, `computed`, `effect`, `ref`). Function-component bodies run exactly once on mount. |
| Components | Class components or function components | Function components only. |
| Children | JSX `<Box><Text/></Box>` | Positional args: `Box(Text("a"), Text("b"), padding=1)`. Strings are only allowed inside `Text`. |
| Async | First-class (Suspense, transitions) | Sync-only. Threads handle concurrency. |
| Yoga layout | C++ via `yoga-layout-prebuilt` | Pure-Python flex subset (column/row, padding/margin/gap, justifyContent/alignItems, basic wrap, width/height). |
| Hooks | `useInput`, `useApp`, `useFocus`, `useInterval`, … | `use_input`, `use_app`, `use_window_size`, `use_interval`, `use_focus`, `use_focus_manager`, `use_context`, `use_box_metrics`. (Phase 2 lands the full ink-equivalent hook surface.) |

See the PRD (`.trellis/tasks/06-19-pyink-mvp/prd.md`) for the design
decisions behind each delta.

## Examples

Twenty-three runnable examples live under [`examples/`](./examples), each
modelled after ink's own examples:

| Example | What it demonstrates | Run |
| --- | --- | --- |
| [`counter`](./examples/counter/counter.py) | Signals + background-thread ticks + reactive repaint. | `python examples/counter/counter.py` |
| [`select-input`](./examples/select-input/select_input.py) | `use_input` arrow navigation + `signal`-driven highlight + `use_app().exit`. | `python examples/select-input/select_input.py` |
| [`borders`](./examples/borders/borders.py) | All four `borderStyle` values side by side. | `python examples/borders/borders.py` |
| [`static`](./examples/static/static.py) | `Static` permanent-output region coexisting with a live counter. | `python examples/static/static.py` |
| [`use-input`](./examples/use-input/use_input_demo.py) | Captures every keystroke and shows the parsed flag set. | `python examples/use-input/use_input_demo.py` |
| [`use-focus`](./examples/use-focus/use_focus_demo.py) | Tab-driven focus between two boxes using `signal` directly (MVP-era hand-rolled version). | `python examples/use-focus/use_focus_demo.py` |
| [`debug-input`](./examples/debug-input/debug_input.py) | Diagnostic tool that echoes every received key + parsed flags. | `python examples/debug-input/debug_input.py` |
| [`alternate-screen`](./examples/alternate-screen/alternate_screen.py) | `render(tree, alternate_screen=True)` — full-screen UI with scrollback preserved on exit. | `python examples/alternate-screen/alternate_screen.py` |
| [`transform`](./examples/transform/transform_demo.py) | `Transform` component — uppercase, hanging indent, line numbering. | `python examples/transform/transform_demo.py` |
| [`computed-batch`](./examples/computed-batch/computed_batch.py) | `computed` derived state + `batch` write coalescing (5 writes, 1 effect run). | `python examples/computed-batch/computed_batch.py` |
| [`nested-layout`](./examples/nested-layout/nested_layout.py) | Outer column → row → inner columns with `flexGrow` (sidebar + main content). | `python examples/nested-layout/nested_layout.py` |
| [`ansi-colors`](./examples/ansi-colors/ansi_colors.py) | Every named colour + hex/rgb/ansi256 + every text style (bold/italic/underline/...). | `python examples/ansi-colors/ansi_colors.py` |
| [`use-window-size`](./examples/use-window-size/use_window_size.py) | `use_window_size` reacting to terminal resize, with width-driven layout switch. | `python examples/use-window-size/use_window_size.py` |
| [`spinner`](./examples/spinner/spinner_demo.py) | `Spinner` external — a gallery of frame sequences (dots / line / arc / moon / star) each in a different colour. | `python examples/spinner/spinner_demo.py` |
| [`link`](./examples/link/link_demo.py) | `Link` external — OSC 8 hyperlinks (URL + `file://`), with colour + underline styling. | `python examples/link/link_demo.py` |
| [`divider`](./examples/divider/divider_demo.py) | `Divider` external — horizontal lines, labelled sections, multiple border styles, vertical divider inside a row. | `python examples/divider/divider_demo.py` |
| [`use-focus-real`](./examples/use-focus-real/use_focus_real_demo.py) | The real `use_focus` + `use_focus_manager` hooks — Tab / Shift+Tab cycle + digit-key jumps between three focusable boxes. | `python examples/use-focus-real/use_focus_real_demo.py` |
| [`measure-element`](./examples/measure-element/measure_demo.py) | `measure_element` API + `use_box_metrics` hook — live `Width × Height` and width-driven content switch on terminal resize. | `python examples/measure-element/measure_demo.py` |
| [`streaming-text`](./examples/streaming-text/streaming_text_demo.py) | `StreamingText` external — a background-thread token stream with **instant** vs **smooth** (`reveal_speed=20`) side-by-side panels. | `python examples/streaming-text/streaming_text_demo.py` |
| [`highlighted-code`](./examples/highlighted-code/highlighted_code_demo.py) | `HighlightedCode` external — Python / JavaScript / SQL / JSON blocks with `line_numbers` and a custom-token-colour `theme` override. Requires `pip install pyink[highlight]`. | `python examples/highlighted-code/highlighted_code_demo.py` |
| [`markdown`](./examples/markdown/markdown_demo.py) | `Markdown` external — every supported block (headings, lists, quote, code block, table, horizontal rule). Requires `pip install pyink[markdown]`. | `python examples/markdown/markdown_demo.py` |
| [`diff`](./examples/diff/diff_demo.py) | `StructuredDiff` external — three variants of a Python-module diff: default context, zero-context, and plain-text fallback. | `python examples/diff/diff_demo.py` |
| [`markdown-streaming`](./examples/markdown-streaming/markdown_streaming_demo.py) | Advanced integration: live AI token stream + `Markdown` re-parsing on every character. Requires `pip install pyink[all]`. | `python examples/markdown-streaming/markdown_streaming_demo.py` |

Most examples wait for `Ctrl+C` (the default `exit_on_ctrl_c=True`).
Press `Ctrl+C` to quit any of them. The Phase 2 / Phase 3 examples
additionally accept `Esc`.

## Development

```bash
python -m pytest tests -v          # ~892 tests (unit + integration)
python -m mypy src/pyink tests examples
python -m ruff check src/pyink tests examples
```

## License

MIT. See [`LICENSE`](./LICENSE).
