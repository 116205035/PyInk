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

MVP complete. The reactive core, layout engine, built-in components,
hooks, live render pipeline and examples all ship in this repository.
See the project PRD (`.trellis/tasks/06-19-pyink-mvp/prd.md`) for the
full roadmap and design decisions.

## Install (editable)

```bash
cd D:/Projects/PyInk
pip install -e ".[dev]"
```

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

All three must be called from inside a function-component body mounted
via `pyink.render.render`:

| Name | Description |
| --- | --- |
| `use_input(handler, *, is_active=True)` | Subscribe `handler(key: Key)` to keyboard events. Returns a dispose callable. |
| `use_app()` | Returns an `AppHandle` with `exit(code)` and `wait_until_render_flush()`. |
| `use_window_size()` | Returns the current terminal size as a `WindowSize(columns, rows)` snapshot. |

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
| Hooks | `useInput`, `useApp`, `useFocus`, `useInterval`, … | `use_input`, `use_app`, `use_window_size`. (No `use_focus` / `use_interval` yet — see examples for hand-rolled equivalents.) |

See the PRD (`.trellis/tasks/06-19-pyink-mvp/prd.md`) for the design
decisions behind each delta.

## Examples

Six runnable examples live under [`examples/`](./examples), each
modelled after ink's own examples:

| Example | What it demonstrates | Run |
| --- | --- | --- |
| [`counter`](./examples/counter/counter.py) | Signals + background-thread ticks + reactive repaint. | `python examples/counter/counter.py` |
| [`select-input`](./examples/select-input/select_input.py) | `use_input` arrow navigation + `signal`-driven highlight + `use_app().exit`. | `python examples/select-input/select_input.py` |
| [`borders`](./examples/borders/borders.py) | All four `borderStyle` values side by side. | `python examples/borders/borders.py` |
| [`static`](./examples/static/static.py) | `Static` permanent-output region coexisting with a live counter. | `python examples/static/static.py` |
| [`use-input`](./examples/use-input/use_input_demo.py) | Captures every keystroke and shows the parsed flag set. | `python examples/use-input/use_input_demo.py` |
| [`use-focus`](./examples/use-focus/use_focus_demo.py) | Tab-driven focus between two boxes using `signal` directly. | `python examples/use-focus/use_focus_demo.py` |

Most examples wait for `Ctrl+C` (the default `exit_on_ctrl_c=True`).
Press `Ctrl+C` to quit any of them.

## Development

```bash
python -m pytest tests -v          # ~500 tests (unit + integration)
python -m mypy src/pyink tests examples
python -m ruff check src/pyink tests examples
```

## License

MIT. See [`LICENSE`](./LICENSE).
