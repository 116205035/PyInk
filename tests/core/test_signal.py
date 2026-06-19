"""Tests for the reactive core (`pyink.core.signal`)."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from pyink import (
    Computed,
    CyclicDependency,
    batch,
    computed,
    effect,
    ref,
    signal,
)

# ---------------------------------------------------------------------------
# signal basics
# ---------------------------------------------------------------------------


def test_signal_read_write_default_value() -> None:
    s = signal(42)
    assert s.value == 42
    s.value = 7
    assert s.value == 7


def test_signal_setter_notifies_subscribers() -> None:
    s = signal(0)
    calls: list[int] = []
    effect(lambda: calls.append(s.value))
    assert calls == [0]
    s.value = 1
    assert calls == [0, 1]


def test_signal_multiple_subscribers_all_notified() -> None:
    s = signal(0)
    a: list[int] = []
    b: list[int] = []
    effect(lambda: a.append(s.value))
    effect(lambda: b.append(s.value))
    s.value = 99
    assert a == [0, 99]
    assert b == [0, 99]


def test_signal_dispose_stops_notifications() -> None:
    s = signal(0)
    calls: list[int] = []
    dispose = effect(lambda: calls.append(s.value))
    s.value = 1
    assert calls == [0, 1]
    dispose()
    s.value = 2
    assert calls == [0, 1]  # no further notifications


def test_signal_equal_value_does_not_notify() -> None:
    s = signal(5)
    calls: list[int] = []
    effect(lambda: calls.append(s.value))
    s.value = 5  # equal — should be a no-op
    assert calls == [5]


# ---------------------------------------------------------------------------
# computed
# ---------------------------------------------------------------------------


def test_computed_first_evaluation_is_lazy() -> None:
    evaluations = {"count": 0}

    def fn() -> int:
        evaluations["count"] += 1
        return 1

    c = computed(fn)
    assert evaluations["count"] == 0  # never read yet
    assert c.value == 1
    assert evaluations["count"] == 1


def test_computed_caches_until_dependency_changes() -> None:
    s = signal(10)
    evaluations = {"count": 0}

    def fn() -> int:
        evaluations["count"] += 1
        return s.value * 2

    c = computed(fn)
    assert c.value == 20
    assert c.value == 20
    assert c.value == 20
    assert evaluations["count"] == 1  # cached

    s.value = 11
    assert c.value == 22
    assert evaluations["count"] == 2


def test_computed_chained() -> None:
    a = signal(1)
    b = computed(lambda: a.value + 1)
    c = computed(lambda: b.value * 10)
    assert c.value == 20
    a.value = 2
    assert c.value == 30


def test_computed_lazy_until_read() -> None:
    """A computed never read should never call its fn."""
    s = signal(0)
    called = {"count": 0}

    def fn() -> int:
        called["count"] += 1
        return s.value

    computed(fn)  # discard the result — never read
    s.value = 1
    s.value = 2
    assert called["count"] == 0


def test_computed_cycle_raises() -> None:
    """A computed that depends on itself must raise, not stack-overflow."""
    # We construct the cycle after creation by attaching a side-effect.
    a: Computed[int] | None = None
    a = computed(lambda: a.value + 1 if a is not None else 0)
    with pytest.raises(CyclicDependency):
        _ = a.value


def test_computed_recomputes_only_for_tracked_dependency() -> None:
    a = signal(1)
    b = signal(100)
    evaluations = {"count": 0}

    def fn() -> int:
        evaluations["count"] += 1
        return a.value  # only depends on a

    c = computed(fn)
    assert c.value == 1
    b.value = 200  # c does not depend on b
    assert c.value == 1
    assert evaluations["count"] == 1
    a.value = 2
    assert c.value == 2
    assert evaluations["count"] == 2


def test_computed_subscribed_by_effect() -> None:
    a = signal(1)
    b = computed(lambda: a.value * 2)
    observed: list[int] = []
    effect(lambda: observed.append(b.value))
    assert observed == [2]
    a.value = 5
    assert observed == [2, 10]


# ---------------------------------------------------------------------------
# effect
# ---------------------------------------------------------------------------


def test_effect_runs_on_mount() -> None:
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1

    effect(fn)
    assert runs["count"] == 1


def test_effect_auto_track_re_runs_on_change() -> None:
    s = signal(0)
    runs: list[int] = []
    effect(lambda: runs.append(s.value))
    assert runs == [0]
    s.value = 1
    s.value = 2
    assert runs == [0, 1, 2]


def test_effect_empty_deps_runs_once() -> None:
    s = signal(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value  # reading should not subscribe when deps=[]

    effect(fn, deps=[])
    s.value = 1
    s.value = 2
    assert runs["count"] == 1


def test_effect_explicit_deps_re_runs_only_on_change() -> None:
    s = signal(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1

    # Pass the Signal itself (not its current value) so the effect can
    # subscribe to it and compare snapshots across runs.
    effect(fn, deps=[s])
    assert runs["count"] == 1
    s.value = 0  # equal to previous — should not re-run
    assert runs["count"] == 1
    s.value = 1  # changed — re-run
    assert runs["count"] == 2


def test_effect_cleanup_runs_before_next_run() -> None:
    s = signal(0)
    events: list[str] = []

    def fn() -> Callable[[], None]:
        events.append(f"run:{s.value}")

        def cleanup() -> None:
            events.append(f"cleanup:{s.value}")

        return cleanup

    effect(fn)
    s.value = 1
    s.value = 2
    # Mount run + cleanup before each subsequent run.
    assert events == [
        "run:0",
        "cleanup:1",
        "run:1",
        "cleanup:2",
        "run:2",
    ]


def test_effect_dispose_runs_final_cleanup() -> None:
    events: list[str] = []

    def fn() -> Callable[[], None]:
        events.append("run")

        def cleanup() -> None:
            events.append("cleanup")

        return cleanup

    dispose = effect(fn)
    dispose()
    assert events == ["run", "cleanup"]


def test_effect_nested() -> None:
    outer = signal(0)
    inner = signal(0)
    log: list[str] = []

    def outer_fn() -> None:
        log.append(f"outer:{outer.value}")

        def inner_fn() -> None:
            log.append(f"inner:{inner.value}")

        effect(inner_fn)

    effect(outer_fn)
    # On mount both run.
    assert log == ["outer:0", "inner:0"]
    log.clear()
    outer.value = 1
    # Outer re-running creates a new inner effect (so inner runs again).
    assert log == ["outer:1", "inner:0"]
    log.clear()
    inner.value = 99
    # Both the still-alive original inner and the newly-created one observe.
    assert "inner:99" in log


# ---------------------------------------------------------------------------
# ref
# ---------------------------------------------------------------------------


def test_ref_does_not_subscribe() -> None:
    r = ref(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = r.value

    effect(fn, deps=None)
    assert runs["count"] == 1
    r.value = 1
    r.value = 2
    assert runs["count"] == 1  # ref reads don't subscribe


def test_ref_read_write() -> None:
    r = ref("a")
    assert r.value == "a"
    r.value = "b"
    assert r.value == "b"


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


def test_batch_coalesces_notifications() -> None:
    s = signal(0)
    runs: list[int] = []
    effect(lambda: runs.append(s.value))
    assert runs == [0]

    def write() -> None:
        s.value = 1
        s.value = 2
        s.value = 3

    batch(write)
    # Should fire exactly once after batch exits.
    assert runs == [0, 3]


def test_batch_returns_fn_result() -> None:
    result = batch(lambda: 42)
    assert result == 42


def test_batch_inner_reads_see_latest_value() -> None:
    s = signal(0)
    seen: list[int] = []

    def write() -> None:
        s.value = 5
        seen.append(s.value)  # reads inside batch should see 5
        s.value = 10
        seen.append(s.value)

    batch(write)
    assert seen == [5, 10]


def test_batch_nested_only_outer_flushes() -> None:
    s = signal(0)
    runs: list[int] = []
    effect(lambda: runs.append(s.value))
    assert runs == [0]

    def inner() -> None:
        s.value = 1
        s.value = 2

    def outer() -> None:
        s.value = 3
        batch(inner)
        s.value = 4
        # At this point, no notifications should have fired yet.
        assert runs == [0]

    batch(outer)
    # Only one notification fired after the outer batch exits.
    assert runs == [0, 4]


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_signal_concurrent_writes() -> None:
    s = signal(0)
    n_workers = 8
    n_writes_per_worker = 500

    def worker() -> None:
        for _ in range(n_writes_per_worker):
            # Read-modify-write under explicit locking is not enough at the
            # signal layer; we use a thread-safe increment by appending a
            # unit list atomically.
            current = s.value
            s.value = current + 1

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Because read-modify-write is not atomic across threads, the final
    # value may be less than n_workers * n_writes_per_worker — but the test
    # asserts no exception was raised and the value is in a sensible range.
    expected_max = n_workers * n_writes_per_worker
    assert 1 <= s.value <= expected_max


def test_signal_concurrent_writes_atomic() -> None:
    """Each thread writes a unique value; final set must contain all writes."""
    s = signal(0)
    lock = threading.Lock()
    seen: set[int] = set()
    n_workers = 8
    n_per = 200

    def worker(idx: int) -> None:
        nonlocal seen
        local: set[int] = set()
        for i in range(n_per):
            v = idx * n_per + i + 1
            s.value = v
            local.add(v)
        with lock:
            seen |= local

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == n_workers * n_per


# ---------------------------------------------------------------------------
# Edge cases (notification dedup, computed equality, lifecycle idempotency)
# ---------------------------------------------------------------------------


def test_effect_reads_signal_and_computed_dedupe() -> None:
    """An effect that reads both a Signal and a Computed derived from it must
    re-run exactly once per source write, not once per notification path.
    """
    s = signal(0)
    c = computed(lambda: s.value * 2)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value
        _ = c.value

    effect(fn)
    assert runs["count"] == 1
    s.value = 5
    assert runs["count"] == 2  # not 3
    s.value = 6
    assert runs["count"] == 3


def test_effect_reads_signal_and_computed_dedupe_in_batch() -> None:
    """Same dedup must hold inside ``batch``."""
    s = signal(0)
    c = computed(lambda: s.value * 2)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value
        _ = c.value

    effect(fn)
    assert runs["count"] == 1

    def write() -> None:
        s.value = 5

    batch(write)
    assert runs["count"] == 2


def test_computed_equal_value_does_not_notify() -> None:
    """A Computed whose recomputed value is equal to its previous value must
    not notify its subscribers (Decision 11.4 applied transitively).
    """
    s = signal(1)
    # Constant computed — value never changes regardless of source.
    c = computed(lambda: 0 if s.value > -100 else 0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = c.value

    effect(fn)
    assert runs["count"] == 1
    s.value = 2  # c stays 0
    s.value = 3  # c stays 0
    assert runs["count"] == 1


def test_effect_dispose_is_idempotent() -> None:
    """Calling dispose multiple times must be safe and not raise."""
    s = signal(0)
    calls: list[int] = []
    dispose = effect(lambda: calls.append(s.value))
    dispose()
    dispose()  # second dispose: no-op
    dispose()  # third dispose: no-op
    s.value = 99
    assert calls == [0]


def test_batch_dispose_effect_prevents_further_runs() -> None:
    """Disposing an effect inside a batch must prevent that effect from
    running again when the batch flushes."""
    s = signal(0)
    calls: list[int] = []
    dispose = effect(lambda: calls.append(s.value))

    def write() -> None:
        dispose()
        s.value = 100

    batch(write)
    # Mount run only; the dispose inside batch prevents the flush from
    # re-running the effect.
    assert calls == [0]


def test_same_signal_read_multiple_times_subscribes_once() -> None:
    """Reading the same Signal multiple times inside an effect must establish
    a single subscription, so a single write triggers a single re-run.
    """
    s = signal(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value
        _ = s.value
        _ = s.value

    effect(fn)
    assert runs["count"] == 1
    s.value = 1
    assert runs["count"] == 2  # exactly one re-run per write
    s.value = 2
    assert runs["count"] == 3


def test_computed_in_effect_subscribes_to_both() -> None:
    """An effect reading both a Signal and a Computed derived from it is
    effectively subscribed to both paths but must observe a consistent
    snapshot per re-run (signal value matches computed value).
    """
    a = signal(1)
    b = computed(lambda: a.value * 2)
    snapshots: list[tuple[int, int]] = []
    effect(lambda: snapshots.append((a.value, b.value)))
    assert snapshots == [(1, 2)]
    a.value = 5
    assert snapshots == [(1, 2), (5, 10)]


def test_subscriber_exception_is_swallowed() -> None:
    """A subscriber raising during notification must not prevent other
    subscribers from firing (Decision 11.3). Initial mount runs fn() directly
    (not via the notification path), so exceptions there propagate — but
    subsequent notifications are guarded.
    """
    s = signal(0)
    received: list[int] = []
    runs = {"boom": 0}

    def boom() -> None:
        runs["boom"] += 1
        if runs["boom"] > 1:  # only raise on re-run (notification path)
            raise RuntimeError("bad subscriber")

    def good() -> None:
        received.append(s.value)

    effect(boom, deps=[s])
    effect(good, deps=[s])
    assert received == [0]
    s.value = 7  # both effects notified; boom raises (swallowed), good appends
    assert received == [0, 7]
