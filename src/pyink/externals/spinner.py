"""``Spinner`` — animated loading indicator (Phase 2 PR2).

Mirrors :mod:`ink-spinner` (which delegates to :mod:`cli-spinners` for the
frame data). The component owns a :class:`Signal` frame index advanced by
:func:`pyink.hooks.use_interval`; the actual frame character is rendered
lazily by a callable child on :func:`Text`, so a signal write
automatically triggers a re-render via the render loop's tracking context.

Design (per PRD Decision 3):

* ``Spinner`` is a factory returning an :class:`Element` whose ``type``
  is a function component. The factory itself never runs hooks — only
  the wrapped function does, when the reconciler mounts it. This lets
  callers write ``Box(Spinner(type="dots"), Text("Loading..."))``
  directly without worrying about render-context boundaries.
* ``frame_index`` is a writable :class:`Signal`. The interval worker
  writes to it from a daemon thread; the render loop subscribes to it
  through the callable child and re-paints on change.
* The callable child ``lambda: frames[frame_index.value]`` is evaluated
  inside the layout pass — exactly the same pattern
  ``test_reactive_props`` exercises for callable style props.
* ``len(frames) <= 1`` short-circuits the tick so we never write a
  no-op value (which would be swallowed by the signal anyway, but
  avoiding the write keeps the worker idle for single-frame spinners).
* Unknown ``type`` falls back to ``"dots"`` rather than raising —
  matches ink-spinner's tolerance of bad names (it just renders
  ``undefined`` there; we render dots).

PR2 scope: this module ships Spinner only. ``interval_ms`` defaults to
``80`` ms (cli-spinners' canonical ``dots`` interval), not per-spinner —
callers who want the upstream interval can pass it explicitly.
"""

from __future__ import annotations

from typing import Any

from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Signal, signal
from pyink.hooks.interval import use_interval

__all__ = ["SPINNERS", "Spinner"]

#: Canonical frame sequences, mirroring ``cli-spinners`` /
#: ``ink-spinner``. Names follow the upstream identifiers so users can
#: copy-paste from JS code. Each value is the tuple of frames to cycle
#: through. Emoji-heavy spinners (``monkey`` / ``smiley`` /
#: ``bouncingBall``) are simplified to keep PyInk dependency-free — the
#: upstream uses Nerd-Font / Unicode glyphs that aren't worth shipping a
#: font fallback for in Phase 2.
SPINNERS: dict[str, tuple[str, ...]] = {
    "dots": ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
    "dots2": ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"),
    "dots3": ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
    "dots4": (
        "⢀", "⣀", "⣄", "⣤", "⣦", "⣶", "⣷", "⣾",
        "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",
    ),
    "dots5": ("⠁", "⠂", "⠄", "⠂"),
    "dots6": ("⠁", "⠉", "⠙", "⠚", "⠒", "⠂", "⠂", "⠒", "⠲", "⠴", "⠤", "⠠", "⠠", "⠤", "⠄", "⠄"),
    "dots7": (
        "⠈", "⠉", "⠋", "⠛", "⠟", "⠿",
        "⠟", "⠛", "⠋", "⠉", "⠈",
    ),
    "dots8": ("⠁", "⠉", "⠙", "⠚", "⠒", "⠂", "⠂", "⠒", "⠲", "⠴", "⠤", "⠠"),
    "dots9": ("⢹", "⢺", "⢼", "⣸", "⣇", "⡧", "⡗", "⡏"),
    "dots10": ("⢄", "⢂", "⢁", "⡁", "⡈", "⡐", "⡠"),
    "dots11": ("⠁", "⠂", "⠄", "⠂"),
    "dots12": ("⢀", "⣀", "⣄", "⣤", "⣦", "⣶", "⣷", "⣾"),
    "line": ("-", "\\", "|", "/"),
    "line2": ("⠂", "-", "–", "—", "–", "-"),
    "pipe": ("┤", "┘", "┴", "└", "├", "┌", "┬", "┐"),
    "simple": ("🎀", "🎁", "🎗"),
    "simpleDots": (".  ", ".. ", "..."),
    "simpleDotsScrolling": (".  ", ".. ", "...", " ..", "  .", "   "),
    "star": ("✶", "✸", "✹", "✺", "✹", "✷"),
    "star2": ("+", "x", "*"),
    "flip": ("_", "_", "_", "-", "`", "'", "´", "-"),
    "hamburger": ("☱", "☲", "☴"),
    "grow": ("▁", "▃", "▄", "▅", "▆", "▇", "▆", "▅", "▄", "▃"),
    "box": ("▖", "▘", "▝", "▗"),
    "bouncingBall": ("●", "●", "●", "●", "●", "●", "●", "●"),
    "smiley": ("avors", "avorz", "avor"),
    "monkey": ("🙈", "🙉", "🙊"),
    "moon": ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"),
    "arc": ("◜", "◠", "◝", "◞", "◡", "◟"),
    "circle": ("◡", "⊙", "⊙", "◠"),
    "squareCorners": ("◢", "◣", "◤", "◥"),
    "triangle": ("◢", "◣", "◤", "◥"),
    "balls": ("● ●", " ● ", " ● ●", "  ●"),
    "dqpb": ("d", "q", "p", "b"),
    "weather": (
        "☀️ ", "☀️ ", "☀️ ", "🌤 ", "⛅️", "🌥 ", "☁️ ", "🌧 ", "🌨 ", "🌧 ",
        "🌨 ", "🌧 ", "🌨 ", "⛈️", "🌨 ", "🌧 ", "🌨 ", "☁️ ", "🌥 ", "⛅️",
        "🌤 ", "☀️ ", "☀️ ",
    ),
    "christmas": ("🎄", "🎄", "🎁", "🎁", "Santa", "🎁", "🎁", "🎄", "🎄"),
    "aesthetic": ("▰▱▱▱▱▱▱", "▰▰▱▱▱▱▱", "▰▰▰▱▱▱▱", "▰▰▰▰▱▱▱", "▰▰▰▰▰▱▱", "▰▰▰▰▰▰▱", "▰▰▰▰▰▰▰"),
    "taphead": ("◌", "◍", "◎", "◉", "◍", "◌"),
    "toggle": ("⊶", "⊷"),
    "toggle2": ("▫", "▪"),
    "toggle3": ("□", "■"),
    "toggle4": ("■", "□", "▪", "▫"),
    "toggle5": (
        " metabar ", " metabar ", " metabar ", " metabar ",
        "Metabar ", " MetaBar ", " Metabar ", " Metabar ",
    ),
    "toggle6": (" =", " = ", "= ", " = ", " =", " = "),
    "toggle7": ("=  ", "== ", " ==", " =="),
    "toggle8": (
        "  *", "  */", " *-", "*-+", "+-+", "+++",
        "++*", "** ", "***", "***", "** ", "*  ", "   ",
    ),
    "toggle9": (" █", " █ ", "█ ", " █ ", " █", " █ "),
    "toggle10": ("(", "(", "(", ")", ")", ")", "|"),
    "toggle11": ("(+)", "(-)", "(+)", "(-)", "(+)", "(-)", "(+)"),
    "toggle12": (" _", " _", "_ ", "_ ", " _", " _"),
    "toggle13": ("*=", "*-", "*+", "**", "*/", "*/"),
}

#: Default tick interval in milliseconds. Matches cli-spinners' canonical
#: ``dots`` interval; callers can pass their own for tighter / looser feel.
_DEFAULT_INTERVAL_MS: int = 80


def _SpinnerComponent(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Reads ``type`` / ``color`` / ``interval_ms`` from ``props`` (passed
    through by :func:`Spinner`). Hooks are invoked here because the
    reconciler sets up the active-component ContextVar before calling
    this function.
    """
    spinner_type: str = props.get("type", "dots")
    color: str | None = props.get("color")
    interval_ms: int = props.get("interval_ms", _DEFAULT_INTERVAL_MS)

    frames = SPINNERS.get(spinner_type, SPINNERS["dots"])
    # Defensive: a registered entry should never be empty, but if a future
    # caller monkey-patches SPINNERS we'd rather render a blank than
    # raise an IndexError on the modulo below.
    if len(frames) == 0:
        frames = (" ",)
    frame_count = len(frames)

    index: Signal[int] = signal(0)

    def tick() -> None:
        # Avoid no-op writes when there's only one frame — the signal
        # would swallow the write anyway (same value), but skipping it
        # keeps the worker truly idle for single-frame spinners.
        if frame_count > 1:
            index.value = (index.value + 1) % frame_count

    use_interval(tick, interval_ms)

    # Callable child: evaluated during layout so the signal read
    # establishes a subscription. The render loop re-paints on every
    # write.
    return Text(lambda: frames[index.value], color=color)


def Spinner(
    *,
    type: str = "dots",
    color: str | None = None,
    interval_ms: int = _DEFAULT_INTERVAL_MS,
) -> Element:
    """Render an animated loading spinner.

    Parameters
    ----------
    type:
        Frame sequence name (see :data:`SPINNERS`). Unknown names
        silently fall back to ``"dots"`` so a typo never crashes the UI.
    color:
        Optional :func:`Text` colour spec (``"red"``, ``"#ff0000"``,
        ``"rgb(255,0,0)"``, ``"ansi256(9)"``). Passed through unchanged.
    interval_ms:
        Tick interval between frames in milliseconds. Defaults to 80 ms
        (cli-spinners' canonical ``dots`` interval). ``<= 0`` is treated
        as "do not start" by :func:`use_interval` — the spinner then
        shows ``frames[0]`` statically.

    Returns
    -------
    Element
        An element whose ``type`` is a function component
        (:func:`_SpinnerComponent`). The factory itself never runs
        hooks — the wrapped function is invoked by the reconciler on
        mount, which is what makes ``Box(Spinner(...), Text(...))`` safe
        to call from outside a render context.

    Usage
    -----
    ::

        Box(
            Spinner(type="dots", color="green"),
            Text(" Loading..."),
        )
    """
    return create_element(
        _SpinnerComponent,
        type=type,
        color=color,
        interval_ms=interval_ms,
    )
