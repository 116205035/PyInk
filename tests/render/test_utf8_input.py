"""Regression tests for multi-byte UTF-8 input handling (Bug 9).

CJK / emoji characters arrive from the kernel as multi-byte UTF-8
sequences that may be split across two ``os.read`` / ``msvcrt.getch``
chunks. The Terminal wraps every raw chunk in an incremental UTF-8
decoder so a partial codepoint is buffered internally and reassembled
before reaching the key parser; without that layer each byte surfaced
as a separate ``Key`` event whose ``input`` was one junk byte, which
TextInput rendered as mojibake.

These tests exercise:

* Single-codepoint inputs of 1/2/3/4 UTF-8 bytes (ASCII, ``é``, ``你``,
  emoji).
* Split delivery — feeding the lead byte and continuation bytes in
  separate chunks must still produce a single ``Key`` carrying the full
  character.
* Mixed input (ASCII + CJK + emoji in one stream).
* Integration: TextInput accumulates the buffer correctly when the
  reader sees split UTF-8 bytes.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from pyink import Box, Text, create_element, render, use_input
from pyink.core.element import Element
from pyink.externals import TextInput
from pyink.render import terminal as _term_mod
from pyink.render.instance import Instance
from pyink.render.keys import Key
from pyink.render.terminal import Terminal


class _FakeTTY(io.StringIO):
    """A StringIO that pretends to be a TTY."""

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _patch_input(
    bytes_iter: Iterator[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive synthetic raw bytes through the Terminal input loop.

    Patches ``_read_stdin_chunk`` so each call pulls the next element of
    ``bytes_iter``; this is the lowest-level seam the Terminal uses, so
    the incremental UTF-8 decoder has to do its job correctly for any
    multi-byte sequence to come out right.
    """

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

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)


def _drive_until(
    received: list[Key], count: int, timeout: float = 2.0
) -> None:
    """Spin until ``received`` has at least ``count`` items."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(received) >= count:
            return
        time.sleep(0.02)


def _mount_with_input(
    on_key: Callable[[Key], None],
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[bytes],
) -> Instance:
    """Render a host that wires ``on_key`` via use_input and feed ``chunks``.

    ``render`` auto-starts the Terminal input reader thread once mount
    finishes (see :func:`pyink.render.pipeline.render`), so the chunks
    fed through the patched ``_read_stdin_chunk`` reach the handler
    without any extra plumbing.
    """

    def Host() -> Element:
        use_input(on_key)
        return Text("hi")

    _patch_input(iter(chunks), monkeypatch)
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    return render(
        create_element(Host),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=10,
        exit_on_ctrl_c=False,
    )


# ---------------------------------------------------------------------------
# Unit: the Terminal decoder reassembles split UTF-8 sequences.
# ---------------------------------------------------------------------------


def test_decode_utf8_single_call() -> None:
    """A single chunk carrying a full codepoint decodes immediately."""
    term = Terminal(stdout=_FakeTTY())
    # The decoder is created lazily in __init__; no raw-mode entry needed
    # for direct decode calls.
    assert term._decode_utf8("你".encode()) == "你"


def test_decode_utf8_split_across_chunks() -> None:
    """Splitting ``你`` into ``\\xe4`` then ``\\xbd\\xa0`` still yields one char."""
    term = Terminal(stdout=_FakeTTY())
    encoded = "你".encode()
    assert len(encoded) == 3
    # First chunk: lead byte only — decoder buffers, returns nothing.
    first = term._decode_utf8(encoded[:1])
    assert first == ""
    # Second chunk: continuation bytes — decoder completes the codepoint.
    second = term._decode_utf8(encoded[1:])
    assert second == "你"


def test_decode_utf4_emoji_split() -> None:
    """4-byte emoji split across two reads reassembles into one char."""
    term = Terminal(stdout=_FakeTTY())
    encoded = "🎉".encode()
    assert len(encoded) == 4
    assert term._decode_utf8(encoded[:2]) == ""
    assert term._decode_utf8(encoded[2:]) == "🎉"


def test_decode_utf8_mixed_chunk() -> None:
    """ASCII + 2-byte + 3-byte + 4-byte in one chunk all decode together."""
    term = Terminal(stdout=_FakeTTY())
    payload = "aé你🎉".encode()
    assert term._decode_utf8(payload) == "aé你🎉"


def test_enter_raw_mode_resets_decoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-entering raw mode must not leak buffered bytes from the prior session."""
    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    # Stash a partial lead byte in the decoder.
    assert term._decode_utf8("你".encode()[:1]) == ""
    # Re-enter raw mode resets the decoder — patch the platform helpers
    # so enter_raw_mode succeeds on the fake TTY.
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)
    term.enter_raw_mode()
    # The leftover continuation bytes no longer complete a codepoint —
    # they're replaced by U+FFFD under the ``errors="replace"`` policy,
    # proving the decoder was reset (otherwise we'd get back ``你``).
    result = term._decode_utf8("你".encode()[1:])
    assert "你" not in result
    assert "�" in result


# ---------------------------------------------------------------------------
# End-to-end: the reader thread delivers whole UTF-8 chars as one Key.
# ---------------------------------------------------------------------------


def test_reader_delivers_single_byte_ascii_as_one_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[Key] = []

    def on_key(key: Key) -> None:
        received.append(key)

    inst = _mount_with_input(on_key, monkeypatch, [b"a"])
    try:
        _drive_until(received, 1)
    finally:
        inst.unmount()
    assert len(received) == 1
    assert received[0].input == "a"


def test_reader_delivers_chinese_char_as_one_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feeding ``你`` as a single chunk yields one Key with ``input='你'``."""
    received: list[Key] = []

    def on_key(key: Key) -> None:
        received.append(key)

    inst = _mount_with_input(on_key, monkeypatch, ["你".encode()])
    try:
        _drive_until(received, 1)
    finally:
        inst.unmount()
    assert len(received) == 1, f"expected 1 key, got {received!r}"
    assert received[0].input == "你"


def test_reader_reassembles_split_chinese_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Splitting ``你`` across two chunks still yields one Key carrying '你'."""
    received: list[Key] = []

    def on_key(key: Key) -> None:
        received.append(key)

    encoded = "你".encode()
    inst = _mount_with_input(
        on_key, monkeypatch, [encoded[:1], encoded[1:]]
    )
    try:
        _drive_until(received, 1)
    finally:
        inst.unmount()
    assert len(received) == 1, (
        f"split UTF-8 should yield 1 Key, got {received!r}"
    )
    assert received[0].input == "你"


def test_reader_reassembles_split_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4-byte emoji split 2+2 reassembles into one Key."""
    received: list[Key] = []

    def on_key(key: Key) -> None:
        received.append(key)

    encoded = "🎉".encode()
    inst = _mount_with_input(
        on_key, monkeypatch, [encoded[:2], encoded[2:]]
    )
    try:
        _drive_until(received, 1)
    finally:
        inst.unmount()
    assert len(received) == 1, f"expected 1 key, got {received!r}"
    assert received[0].input == "🎉"


def test_reader_mixed_ascii_cjk_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASCII + 2-byte + 3-byte + 4-byte in one stream deliver as 4 keys."""
    received: list[Key] = []

    def on_key(key: Key) -> None:
        received.append(key)

    chunks = [c.encode("utf-8") for c in ["a", "é", "你", "🎉"]]
    inst = _mount_with_input(on_key, monkeypatch, chunks)
    try:
        _drive_until(received, 4)
    finally:
        inst.unmount()
    assert [k.input for k in received] == ["a", "é", "你", "🎉"], (
        f"got {received!r}"
    )


def test_reader_split_with_ascii_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A split codepoint followed by ASCII must still surface both correctly.

    The reader must not lose the buffered lead byte when subsequent chunks
    arrive — the second chunk completes ``你`` and the third delivers ``a``.
    """
    received: list[Key] = []

    def on_key(key: Key) -> None:
        received.append(key)

    encoded = "你".encode()
    inst = _mount_with_input(
        on_key,
        monkeypatch,
        [encoded[:1], encoded[1:], b"a"],
    )
    try:
        _drive_until(received, 2)
    finally:
        inst.unmount()
    assert [k.input for k in received] == ["你", "a"], f"got {received!r}"


# ---------------------------------------------------------------------------
# Integration: TextInput accumulates Chinese chars correctly.
# ---------------------------------------------------------------------------


def test_text_input_accumulates_chinese_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typing ``你好`` through the reader must populate TextInput with both chars."""
    captured: dict[str, str] = {"value": ""}

    def Host() -> Element:
        def on_change(v: str) -> None:
            captured["value"] = v

        return Box(
            TextInput(
                on_change=on_change,
                placeholder="type chinese",
            )
        )

    _patch_input(
        iter([c.encode("utf-8") for c in ["你", "好"]]), monkeypatch
    )
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Host),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=10,
        exit_on_ctrl_c=False,
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and captured["value"] != "你好":
            time.sleep(0.02)
    finally:
        inst.unmount()
    assert captured["value"] == "你好", (
        f'TextInput should hold "你好", got {captured["value"]!r}'
    )


def test_text_input_handles_split_chinese_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when ``你`` arrives as two raw chunks, TextInput still shows ``你``."""
    captured: dict[str, str] = {"value": ""}

    def Host() -> Element:
        def on_change(v: str) -> None:
            captured["value"] = v

        return Box(TextInput(on_change=on_change))

    encoded = "你".encode()
    _patch_input(iter([encoded[:1], encoded[1:]]), monkeypatch)
    stdout = _FakeTTY()
    stdin = _FakeTTY()
    inst = render(
        create_element(Host),
        stdout=stdout,
        stdin=stdin,
        columns=40,
        rows=10,
        exit_on_ctrl_c=False,
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and captured["value"] != "你":
            time.sleep(0.02)
    finally:
        inst.unmount()
    assert captured["value"] == "你", (
        f'TextInput should hold "你", got {captured["value"]!r}'
    )
