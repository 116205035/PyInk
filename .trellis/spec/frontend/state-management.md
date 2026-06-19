# State Management

> How state is managed in this project.

---

## Overview

PyInk uses a **signals-based reactive model** (similar to SolidJS / Vue 3 / Preact Signals), not React hooks. Components are pure functions that run **once on mount**; state lives in signal objects, and any code that reads a signal automatically subscribes to it.

Five primitives in `pyink.core.signal`:

| Primitive | Purpose | Triggers re-render? |
|-----------|---------|---------------------|
| `signal(initial)` | Mutable observable value | Yes (on write, if value changes) |
| `computed(fn)` | Derived value, lazy + cached | Yes (when result changes) |
| `effect(fn, deps?)` | Side effect, auto-subscribes | N/A (it *causes* renders) |
| `ref(initial)` | Mutable holder, non-reactive | No |
| `batch(fn)` | Coalesce multiple writes into one notification | N/A |

API style: **`.value` property only** for read/write. No `()` call style.

```python
from pyink import signal, computed, effect, ref, batch

count = signal(0)
double = computed(lambda: count.value * 2)

def on_key(k):
    if k == 'up':
        count.value += 1     # write → notifies subscribers

effect(lambda: print(f"count={count.value}, double={double.value}"))
# mount: prints "count=0, double=0"
# count.value = 5 → effect reruns: prints "count=5, double=10"
```

---

## State Categories

### Local state (per-component)

State created inside a function component's closure. Lives as long as the component is mounted.

```python
def Counter():
    count = signal(0)               # local state

    def on_key(k):
        if k == 'up':
            count.value += 1

    use_input(on_key)
    return Text(lambda: str(count.value))
```

### Derived state

Use `computed` for anything that's a pure function of other signals. **Don't** recompute manually in render — let `computed` cache and track dependencies.

```python
# Good
visible_messages = computed(lambda: [
    m for m in messages.value
    if matches_filter(m, filter.value)
])

# Bad (recomputes every render, no caching, no auto-subscription)
def MessageList():
    visible = [m for m in messages.value if ...]   # ❌
    return Box(*[Text(m.content) for m in visible])
```

### Cross-render references (non-reactive)

Use `ref` for mutable values that should **not** trigger re-renders: timer handles, raw-mode flags, caches.

```python
def PollingWidget():
    timer_ref = ref(None)

    def setup():
        timer_ref.value = setInterval(tick, 1000)
        return lambda: clearInterval(timer_ref.value)
    effect(setup, deps=[])

    return Text("...")
```

### Global state

PyInk has no built-in "store" primitive. For app-wide state, create signals at module level and import them.

```python
# jarvis/state.py
messages = signal([])
current_model = signal("claude-3-opus")

# jarvis/components/messages.py
from jarvis.state import messages
```

**Criteria for promoting to global**: state is read/written by 3+ unrelated components.

---

## When to Use Global State

Use module-level signals when:

- Multiple unrelated subtrees need to read the same state (e.g., current user, app config)
- State must persist across component unmount/remount (e.g., navigation state)
- A background thread (e.g., AI stream worker) needs to push data into the UI

Avoid global state when:

- Only one component subtree uses it (use local closure state)
- It's transient (use `ref`)

---

## Server State

PyInk does not manage server state — that's the application's job. The typical pattern for streaming AI responses:

```python
def JarvisApp():
    messages = signal([])

    def stream(text):
        def worker():
            # Runs on background thread; signal writes are thread-safe
            for chunk in fake_stream(text):
                messages.value = [*messages.value, chunk]
        threading.Thread(target=worker, daemon=True).start()

    return Box(*[Text(m) for m in messages.value])
```

Key points:
- Signal writes from worker threads are safe (per-signal `RLock`)
- Use `batch` when pushing multiple chunks at once to avoid re-rendering per chunk
- Don't share signals across processes — they're in-memory only

---

## Patterns

### Read in `computed` / `effect`, write in handlers

```python
# Good: read inside computed/effect (subscribes); write in event handler
def Component():
    count = signal(0)
    filtered = computed(lambda: count.value % 2 == 0)

    def on_key(k):
        if k == 'up':
            count.value += 1     # write — fine, event handler context
    use_input(on_key)

    return Text(lambda: f"even={filtered.value}")
```

### Use `batch` for multi-write atomicity

```python
# Without batch: two notifications, two rerenders
def update_both():
    a.value = 1
    b.value = 2

# With batch: one notification, one rerender
def update_both():
    with batch:
        a.value = 1
        b.value = 2
```

### Dispose effects on unmount

`effect()` returns a `Dispose` callable. The reconciler (PR2) **must** call it when the owning component unmounts, otherwise the effect leaks and keeps firing.

```python
def Component():
    dispose = effect(lambda: print("running"))
    # reconciler stores `dispose` and calls it on unmount
```

---

## Common Mistakes

### 1. Reading signal in render without wrapping in callable/computed

```python
# ❌ Reads count.value at mount time; never updates
def Bad():
    count = signal(0)
    return Text(f"count={count.value}")

# ✅ Wrap in callable (re-evaluated on rerender)
def Good():
    count = signal(0)
    return Text(lambda: f"count={count.value}")

# ✅ Or use computed for derived values
def Good2():
    count = signal(0)
    label = computed(lambda: f"count={count.value}")
    return Text(lambda: label.value)
```

### 2. Writing to a signal during render

```python
# ❌ Triggers rerender during render → infinite loop
def Bad():
    count = signal(0)
    count.value += 1   # write during "render"
    return Text("...")
```

Writes belong in event handlers, effects (with cleanup), or external threads.

### 3. Treating `ref` like `signal`

```python
# ❌ ref doesn't trigger updates — UI won't refresh
def Bad():
    count = ref(0)
    def on_key(k): count.value += 1
    use_input(on_key)
    return Text(lambda: str(count.value))   # stale

# ✅ Use signal for reactive state
count = signal(0)
```

### 4. Creating cycles in `computed`

```python
# ❌ CyclicDependency error
a = computed(lambda: b.value + 1)
b = computed(lambda: a.value + 1)
a.value   # raises CyclicDependency
```

### 5. Forgetting that `effect(deps=[value])` won't retrigger on plain-value changes

```python
# deps must be Signal/Computed objects, not snapshot values
some_text = signal("hi")

# ❌ deps=[some_text.value] — passes "hi" (a str), effect never retriggers
effect(lambda: print(some_text.value), deps=[some_text.value])

# ✅ deps=[some_text] — passes the Signal object, effect retriggers on write
effect(lambda: print(some_text.value), deps=[some_text])
```

### 6. Subscribing inside a loop without cleanup

```python
# ❌ Each iteration leaks an effect subscription
def Bad():
    for item in items.value:
        effect(lambda: print(item.name))  # never disposed
```

Effects created in loops must be tracked and disposed. Prefer `computed` for derived data over effects.

---

## Reference

- Source: `src/pyink/core/signal.py`
- Tests: `tests/core/test_signal.py` (35 cases)
- Design decisions: `.trellis/tasks/06-19-pyink-mvp/prd.md` Decisions 1, 6, 10, 11
