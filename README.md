# PyInk

A Python ink-style TUI framework built on **signals** (no React-style hooks).
Inspired by ink (TS), SolidJS / Vue 3 / Preact Signals reactive model, and the
[Claude Code](https://github.com/anthropic/claude-code) terminal UX.

PyInk targets Python 3.11+, has zero runtime dependencies, and is fully
synchronous (concurrency is handled by application-level threads).

## Status

Pre-alpha. PR1 ships the reactive core only; components, layout, render and
hooks arrive in subsequent PRs. See the project PRD
(`.trellis/tasks/06-19-pyink-mvp/prd.md`) for the roadmap.

## Install (editable)

```bash
cd D:/Projects/PyInk
pip install -e ".[dev]"
```

## Minimal example

```python
from pyink import signal, computed, effect

count = signal(0)
double = computed(lambda: count.value * 2)

effect(lambda: print(f"count is {count.value}, double is {double.value}"))
# -> count is 0, double is 0

count.value = 5
# -> count is 5, double is 10
```

## API surface (PR1)

| Name | Description |
| --- | --- |
| `signal(initial)` | Observable writable value; read with `.value`, write with `.value = x`. |
| `computed(fn)` | Lazy derived value; cached until a dependency changes. |
| `effect(fn, deps=None)` | Side-effect that re-runs on dependency changes. |
| `ref(initial)` | Non-reactive mutable reference for stable handles. |
| `batch(fn)` | Coalesce multiple signal writes into a single notification. |

### `deps` semantics for `effect`

- `deps=None` — auto-track every signal read inside `fn`.
- `deps=[]` — mount effect, runs exactly once.
- `deps=[sig_a, sig_b, ...]` — re-run only when any dep's `.value` changes
  (`!=`). Pass the Signal / Computed object itself, not its current value.

`fn` may return a cleanup callable that runs before the next re-run and on
dispose. `effect(...)` returns a dispose callable.

## Development

```bash
python -m pytest tests -v
python -m mypy src/pyink
python -m ruff check src/pyink tests
```

## License

MIT (TBD).
