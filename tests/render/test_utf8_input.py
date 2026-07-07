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

from ink import Box, Text, create_element, render, use_input
from ink.core.element import Element
from ink.externals import TextInput
from ink.render import terminal as _term_mod
from ink.render.instance import Instance
from ink.render.keys import Key
from ink.render.terminal import Terminal


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
    finishes (see :func:`ink.render.pipeline.render`), so the chunks
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


# ---------------------------------------------------------------------------
# Bug 9 follow-up: Windows console codepage (locale != UTF-8).
#
# On a zh-CN Windows machine the default console input codepage is 936
# (GBK), so the IME emits GBK bytes for CJK characters. The Terminal's
# UTF-8 decoder cannot decode those bytes — they surface as ``U+FFFD``
# replacement chars and the user sees ``??``. The fix is to flip the
# console input + output codepage to UTF-8 (65001) for the duration of
# raw mode and restore the original locale codepage on exit.
#
# These tests run on any platform — we patch ``_PLATFORM`` and a fake
# ``ctypes`` surface so the codepage-switch code runs against mocked
# Win32 calls.
# ---------------------------------------------------------------------------


class _CodepageProbeKernel32:
    """Records every SetConsoleCP / SetConsoleOutputCP call for assertions."""

    def __init__(self, current_input_cp: int = 936, current_output_cp: int = 936) -> None:
        self.current_input_cp = current_input_cp
        self.current_output_cp = current_output_cp
        self.set_input_cp_calls: list[int] = []
        self.set_output_cp_calls: list[int] = []

    def GetStdHandle(self, _handle: int) -> int:
        return 1234

    def GetConsoleMode(self, _handle: int, mode_ref: object) -> int:
        # Legacy echo + line + processed input flags set.
        mode_ref.value = 0xE7  # type: ignore[attr-defined]
        return 1

    def SetConsoleMode(self, _handle: int, _mode: int) -> int:
        return 1

    def GetConsoleCP(self) -> int:
        return self.current_input_cp

    def GetConsoleOutputCP(self) -> int:
        return self.current_output_cp

    def SetConsoleCP(self, cp: int) -> int:
        self.set_input_cp_calls.append(cp)
        self.current_input_cp = cp
        return 1

    def SetConsoleOutputCP(self, cp: int) -> int:
        self.set_output_cp_calls.append(cp)
        self.current_output_cp = cp
        return 1


def _install_fake_ctypes(
    kernel32: _CodepageProbeKernel32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install a fake ``ctypes`` module so the Windows code runs anywhere."""

    class _FakeDWORD:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    class _FakeCtypes:
        windll = type("windll", (), {"kernel32": kernel32})()

        @staticmethod
        def byref(obj: object) -> object:
            return obj

        class wintypes:  # noqa: N801 - mirrors real module name
            DWORD = _FakeDWORD

    import sys

    monkeypatch.setitem(sys.modules, "ctypes", _FakeCtypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", _FakeCtypes.wintypes)


def test_windows_raw_mode_switches_codepage_to_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``enter_raw_mode`` on Windows must flip both codepages to 65001.

    This is the regression test for the original Bug 9 follow-up: without
    this switch a zh-CN user typing ``你`` gets ``??`` because the IME
    emits GBK bytes that the UTF-8 decoder rejects.
    """
    probe = _CodepageProbeKernel32(current_input_cp=936, current_output_cp=936)
    _install_fake_ctypes(probe, monkeypatch)

    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    term._enter_raw_mode_windows()

    assert probe.set_input_cp_calls == [65001], (
        f"input CP must be flipped to UTF-8 (65001), got {probe.set_input_cp_calls!r}"
    )
    assert probe.set_output_cp_calls == [65001], (
        f"output CP must be flipped to UTF-8 (65001), got {probe.set_output_cp_calls!r}"
    )
    # Original codepage must be captured for restore on exit.
    assert term._prev_console_input_cp == 936
    assert term._prev_console_output_cp == 936


def test_windows_raw_mode_exit_restores_locale_codepage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``exit_raw_mode`` must restore the original codepage captured at entry."""
    probe = _CodepageProbeKernel32(current_input_cp=936, current_output_cp=936)
    _install_fake_ctypes(probe, monkeypatch)

    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    term._enter_raw_mode_windows()
    assert probe.current_input_cp == 65001
    assert probe.current_output_cp == 65001

    term._exit_raw_mode_windows()
    assert probe.current_input_cp == 936, (
        "exit must restore the locale input codepage (936) captured at entry"
    )
    assert probe.current_output_cp == 936, (
        "exit must restore the locale output codepage (936) captured at entry"
    )
    # Restore must be idempotent — a second exit must not flip the CP again.
    term._prev_console_input_cp = None
    term._prev_console_output_cp = None
    calls_before = (len(probe.set_input_cp_calls), len(probe.set_output_cp_calls))
    term._exit_raw_mode_windows()
    calls_after = (len(probe.set_input_cp_calls), len(probe.set_output_cp_calls))
    assert calls_before == calls_after, (
        "exit_raw_mode with no saved codepage must not call SetConsoleCP"
    )


def test_windows_raw_mode_idempotent_codepage_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-entering raw mode doesn't double-flip the codepage state.

    ``enter_raw_mode`` itself is idempotent at the Terminal level, but the
    Windows helper can be called directly — every entry must record a
    fresh capture so the matching exit restores the right value.
    """
    probe = _CodepageProbeKernel32(current_input_cp=936, current_output_cp=936)
    _install_fake_ctypes(probe, monkeypatch)

    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    term._enter_raw_mode_windows()
    # After entry the codepage is UTF-8 — a second entry should capture
    # 65001 as the new "previous" state so the eventual restore lands on
    # 65001 instead of the original 936.
    term._enter_raw_mode_windows()
    assert term._prev_console_input_cp == 65001
    assert term._prev_console_output_cp == 65001


def test_windows_codepage_switched_on_raw_mode_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: raw-mode entry on the GBK-locale Windows path invokes SetConsoleCP.

    Drives a fake Windows path with a GBK-input IME: ``_read_stdin_chunk``
    yields GBK bytes for ``你`` (``b"\\xc4\\xe3"``). The assertion only
    checks that ``SetConsoleCP(65001)`` was invoked at entry — after the
    mock the bytes still arrive as GBK (we can't mock the IME encoding
    itself), but the codepage-switch contract is what we're verifying.
    """
    probe = _CodepageProbeKernel32(current_input_cp=936, current_output_cp=936)
    _install_fake_ctypes(probe, monkeypatch)
    monkeypatch.setattr(_term_mod, "_PLATFORM", "windows")

    received: list[Key] = []

    # GBK bytes for "你" — what the IME would emit under codepage 936.
    gbk_ni = "你".encode("gbk")
    chunks = iter([gbk_ni])

    def fake_read(fd: int, n: int) -> bytes:
        try:
            return next(chunks)
        except StopIteration:
            time.sleep(0.05)
            return b""

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    # The Windows raw-mode helper now runs against our fake ctypes.
    # Unix helpers stay as no-ops.
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)

    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    dispose = term.on_key(lambda k: received.append(k))
    term.enable_input()
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not received:
            time.sleep(0.02)
    finally:
        dispose()

    # The codepage-switch contract is the regression target. After entry
    # the kernel32 probe must have seen both UTF-8 switch calls.
    assert 65001 in probe.set_input_cp_calls, (
        f"input CP not switched to UTF-8 at raw-mode entry; "
        f"calls={probe.set_input_cp_calls!r}"
    )
    assert 65001 in probe.set_output_cp_calls, (
        f"output CP not switched to UTF-8 at raw-mode entry; "
        f"calls={probe.set_output_cp_calls!r}"
    )
    # After dispose the locale codepage must be restored.
    assert probe.current_input_cp == 936, (
        f"locale input CP (936) not restored on exit; "
        f"current={probe.current_input_cp}"
    )
    assert probe.current_output_cp == 936, (
        f"locale output CP (936) not restored on exit; "
        f"current={probe.current_output_cp}"
    )


# ---------------------------------------------------------------------------
# Codepage-skip optimisation: don't re-flip when already UTF-8.
#
# conhost re-initialises the screen buffer on a codepage switch — which
# clears the cmd.exe startup banner / scrollback on systems that are
# already configured for UTF-8 (Windows Terminal, Win11 with the "Beta:
# Use Unicode UTF-8..." locale setting). The fix: only call SetConsoleCP
# / SetConsoleOutputCP when the current CP isn't 65001. zh-CN GBK
# systems (CP=936) still get switched — see tests above.
# ---------------------------------------------------------------------------


def test_no_setconsolecp_when_already_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """If both codepages are already 65001, no SetConsole* call is made.

    This is the regression test for the conhost buffer-reset bug: on a
    UTF-8-configured Windows, calling ``SetConsoleCP(65001)`` when the
    CP is already 65001 still triggers conhost's screen-buffer
    re-decode, wiping the cmd.exe startup banner. Skipping the call
    avoids that.
    """
    probe = _CodepageProbeKernel32(
        current_input_cp=65001, current_output_cp=65001
    )
    _install_fake_ctypes(probe, monkeypatch)

    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    term._enter_raw_mode_windows()

    assert probe.set_input_cp_calls == [], (
        f"must NOT call SetConsoleCP when input CP is already 65001; "
        f"calls={probe.set_input_cp_calls!r}"
    )
    assert probe.set_output_cp_calls == [], (
        f"must NOT call SetConsoleOutputCP when output CP is already "
        f"65001; calls={probe.set_output_cp_calls!r}"
    )
    # The original CP must still be captured for restore on exit.
    assert term._prev_console_input_cp == 65001
    assert term._prev_console_output_cp == 65001


def test_setconsolecp_called_when_not_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """GBK-locale (CP=936) systems must still flip both CPs to 65001.

    The IME on a zh-CN Windows emits GBK bytes; the Terminal's UTF-8
    decoder would reject them, so the switch is mandatory here. This
    guards against the optimisation accidentally skipping real
    codepage-mismatch cases.
    """
    probe = _CodepageProbeKernel32(current_input_cp=936, current_output_cp=936)
    _install_fake_ctypes(probe, monkeypatch)

    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    term._enter_raw_mode_windows()

    assert probe.set_input_cp_calls == [65001]
    assert probe.set_output_cp_calls == [65001]
    assert term._prev_console_input_cp == 936
    assert term._prev_console_output_cp == 936


def test_exit_restores_recorded_cp_when_recorded_is_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore path is unchanged: it restores whatever CP was recorded.

    If the CP was already 65001 at entry (so SetConsole was skipped),
    the recorded "previous" CP is 65001 and exit restores to 65001 —
    which is a no-op but still exercises the restore call. This
    confirms the restore path is independent of the entry-skip
    optimisation.
    """
    probe = _CodepageProbeKernel32(
        current_input_cp=65001, current_output_cp=65001
    )
    _install_fake_ctypes(probe, monkeypatch)

    term = Terminal(stdout=_FakeTTY(), stdin=_FakeTTY())
    term._enter_raw_mode_windows()
    # Entry skipped the SetConsole calls, so probe still has no calls.
    assert probe.set_input_cp_calls == []
    assert probe.set_output_cp_calls == []
    # Recorded "previous" CP is 65001.
    assert term._prev_console_input_cp == 65001

    term._exit_raw_mode_windows()
    # Restore must still call SetConsole with the recorded value.
    assert probe.set_input_cp_calls == [65001], (
        f"exit must call SetConsoleCP with the recorded prev CP "
        f"(65001); calls={probe.set_input_cp_calls!r}"
    )
    assert probe.set_output_cp_calls == [65001], (
        f"exit must call SetConsoleOutputCP with the recorded prev "
        f"CP (65001); calls={probe.set_output_cp_calls!r}"
    )
    # Slots cleared for idempotency.
    assert term._prev_console_input_cp is None
    assert term._prev_console_output_cp is None
