"""ConfirmInput example — Y/N confirmation prompts (Phase 4 PR4).

Reference: ink's third-party ``ink-confirm-input`` component. PyInk's
:func:`pyink.externals.ConfirmInput` external supports both the
default single-keystroke mode (``y`` / ``n`` fire immediately) and
the more deliberate ``require_enter=True`` mode where the user picks
a side first and then confirms with Enter.

This example mounts three confirm prompts side by side:

* single-key mode (default) — press ``y`` or ``n`` and the matching
  callback fires immediately.
* ``require_enter=True`` mode — pick a side with ``y`` / ``n`` (the
  highlight moves), then confirm with Enter.
* custom keys (``q`` to confirm, ``a`` to cancel) — same single-key
  shape but with non-default key bindings.

Each prompt reports its most recent outcome on a status line below
itself so the demo doubles as a manual verification harness.

Run::

    python examples/confirm-input/confirm_demo.py

Controls:

* ``y`` / ``n`` (or ``q`` / ``a`` for the custom prompt) — confirm
  or cancel. In ``require_enter`` mode the keystroke only moves the
  highlight; Enter then fires.
* Enter — confirm the highlighted option (``require_enter`` mode).
* Esc / Ctrl+C — quit.
"""

from __future__ import annotations

import sys

from pyink import (
    Box,
    Text,
    create_element,
    render,
    signal,
    use_app,
    use_input,
)
from pyink.core.element import Element
from pyink.core.signal import Signal
from pyink.externals import ConfirmInput
from pyink.render.keys import Key


def _status_signal() -> Signal[str]:
    """Helper kept here for symmetry with the other examples.

    The real implementation just returns a fresh signal — factored out
    so the three prompts below read identically.
    """
    return signal("(no action yet)")


def ConfirmDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        single_status = _status_signal()
        require_status = _status_signal()
        custom_status = _status_signal()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        # Single-key prompt — y / n fire on the keystroke.
        def _single_confirm() -> None:
            single_status.value = "single-key: confirmed"

        def _single_cancel() -> None:
            single_status.value = "single-key: cancelled"

        def _require_confirm() -> None:
            require_status.value = "require_enter: confirmed"

        def _require_cancel() -> None:
            require_status.value = "require_enter: cancelled"

        def _custom_confirm() -> None:
            custom_status.value = "custom keys: confirmed"

        def _custom_cancel() -> None:
            custom_status.value = "custom keys: cancelled"

        single_prompt = ConfirmInput(
            on_confirm=_single_confirm,
            on_cancel=_single_cancel,
            prompt="Single-key (y / n fires immediately)",
            confirm_key="y",
            cancel_key="n",
        )

        # require_enter=True — y / n move the highlight, Enter fires.
        require_prompt = ConfirmInput(
            on_confirm=_require_confirm,
            on_cancel=_require_cancel,
            prompt="require_enter=True — pick a side, then Enter",
            require_enter=True,
            default="confirm",
        )

        # Custom keys — q confirms, a cancels.
        custom_prompt = ConfirmInput(
            on_confirm=_custom_confirm,
            on_cancel=_custom_cancel,
            prompt="Custom keys (q to confirm, a to cancel)",
            confirm_key="q",
            cancel_key="a",
            confirm_label="quit",
            cancel_label="abort",
        )

        return Box(
            Text("ConfirmInput demo", bold=True),
            Text(
                "y / n / q / a fire callbacks; Esc / Ctrl+C quits.",
                dimColor=True,
            ),
            Box(
                single_prompt,
                Text(lambda: single_status.value, dimColor=True),
                flexDirection="column",
                borderStyle="single",
                paddingX=1,
                paddingY=0,
            ),
            Box(
                require_prompt,
                Text(lambda: require_status.value, dimColor=True),
                flexDirection="column",
                borderStyle="single",
                paddingX=1,
                paddingY=0,
            ),
            Box(
                custom_prompt,
                Text(lambda: custom_status.value, dimColor=True),
                flexDirection="column",
                borderStyle="single",
                paddingX=1,
                paddingY=0,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(ConfirmDemo(), columns=60, rows=22)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
