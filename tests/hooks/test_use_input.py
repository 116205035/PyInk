"""Tests for :func:`pyink.hooks.use_input`.

Components mount via :func:`pyink.render.render` so the active-Instance
ContextVar is populated. Synthetic input bytes are fed through the
Terminal's input loop by patching ``_read_stdin_chunk`` + ``_wait_for_input``.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable, Iterator
from typing import cast

import pytest

from pyink import Box, Text, create_element, render, use_input
from pyink.core.element import Element
from pyink.core.signal import Signal, effect, signal
from pyink.render import terminal as _term_mod
from pyink.render.keys import Key
from pyink.render.terminal import Terminal


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _patch_input(bytes_iter: Iterator[bytes], monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``_read_stdin_chunk`` + ``_wait_for_input`` + raw-mode methods."""

    lock = threading.Lock()
    exhausted = {"value": False}

    def fake_read(fd: int, n: int) -> bytes:
        with lock:
            if exhausted["value"]:
                time.sleep(0.05)
                return b""
            try:
                return next(bytes_iter)
            except StopIteration:
                exhausted["value"] = True
                return b""

    # Patch the terminal module's read helper so the same fake works on
    # both Unix (``os.read``) and Windows (``msvcrt.getch``-based) paths.
    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)


def test_use_input_receives_key(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[Key] = []

    def Counter() -> Element:
        def on_key(key: Key) -> None:
            received.append(key)

        use_input(on_key)
        return Text("hi")

    _patch_input(iter([b"a"]), monkeypatch)
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Counter),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    for _ in range(40):
        if received:
            break
        time.sleep(0.025)
    inst.unmount()
    assert received
    assert received[0].input == "a"


def test_use_input_is_active_false_does_not_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[Key] = []

    def Counter() -> Element:
        def on_key(key: Key) -> None:
            received.append(key)

        use_input(on_key, is_active=False)
        return Text("hi")

    _patch_input(iter([b"a"]), monkeypatch)
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Counter),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    for _ in range(10):
        if received:
            break
        time.sleep(0.025)
    inst.unmount()
    assert received == []


def test_use_input_outside_render_raises() -> None:
    with pytest.raises(RuntimeError, match="use_input"):
        use_input(lambda _k: None)


def test_use_input_increments_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration-style: pressing Up increments a counter signal."""
    chunks = iter([b"\x1b[A", b"\x1b[A"])  # two Up arrows
    _patch_input(chunks, monkeypatch)
    captured: dict[str, object] = {}

    def Counter() -> Element:
        count = signal(0)

        def on_key(key: Key) -> None:
            if key.up_arrow:
                count.value += 1

        use_input(on_key)
        captured["count_ref"] = count
        return Text(lambda: str(count.value))

    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Counter),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    for _ in range(120):
        ref = captured.get("count_ref")
        if ref is not None and cast("Signal[int]", ref).value >= 2:
            break
        time.sleep(0.025)
    inst.unmount()
    count_ref = cast("Signal[int]", captured["count_ref"])
    assert count_ref.value == 2


def test_multiple_components_both_receive_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_a: list[Key] = []
    received_b: list[Key] = []

    def A() -> Element:
        use_input(lambda k: received_a.append(k))
        return Text("a")

    def B() -> Element:
        use_input(lambda k: received_b.append(k))
        return Text("b")

    def Root() -> Element:
        return Box(create_element(A), create_element(B))

    def stream() -> Iterator[bytes]:
        while True:
            time.sleep(0.02)
            yield b"x"

    _patch_input(stream(), monkeypatch)
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Root),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    for _ in range(80):
        if received_a and received_b:
            break
        time.sleep(0.025)
    inst.unmount()
    assert received_a
    assert received_b


def test_use_input_dispose_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    """The returned dispose stops the handler from receiving more keys."""
    received: list[Key] = []
    bag: dict[str, object] = {}

    def Counter() -> Element:
        def on_key(key: Key) -> None:
            received.append(key)

        def setup() -> None:
            bag["dispose"] = use_input(on_key)

        effect(setup)
        return Text("hi")

    def stream() -> Iterator[bytes]:
        while True:
            time.sleep(0.02)
            yield b"z"

    _patch_input(stream(), monkeypatch)
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Counter),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    # Wait for at least one key.
    for _ in range(40):
        if received:
            break
        time.sleep(0.025)
    assert received
    received.clear()
    # Dispose the subscription.
    dispose = cast("Callable[[], None]", bag["dispose"])
    dispose()
    # Wait — no more keys should arrive.
    time.sleep(0.1)
    assert received == []
    inst.unmount()


def test_use_input_manual_dispose_then_effect_cleanup_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling manual dispose before component unmount must be a no-op on
    the second invocation (no double-unsubscribe, no exception). This
    guards against the double-dispose regression where ``dispose()`` and
    the effect-cleanup path both invoke the same underlying
    ``dispose_subscribe`` + ``effect_dispose``.
    """
    received: list[Key] = []
    bag: dict[str, object] = {}

    def Counter() -> Element:
        def on_key(key: Key) -> None:
            received.append(key)

        def setup() -> None:
            bag["dispose"] = use_input(on_key)

        effect(setup)
        return Text("hi")

    def stream() -> Iterator[bytes]:
        while True:
            time.sleep(0.02)
            yield b"z"

    _patch_input(stream(), monkeypatch)
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Counter),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    # Wait for the subscription to register + deliver at least one key.
    for _ in range(40):
        if received:
            break
        time.sleep(0.025)
    assert received, "handler never fired before dispose"
    received.clear()

    dispose = cast("Callable[[], None]", bag["dispose"])
    # First call tears down the subscription + the effect binding.
    dispose()
    # Second manual call must be a no-op (guard flag is set).
    dispose()
    # Wait to confirm no further keys arrive.
    time.sleep(0.1)
    assert received == [], "handler still firing after manual dispose"

    # Unmount triggers the effect-cleanup path; because dispose already
    # ran, this must also be a no-op (no error, no warning).
    inst.unmount()
