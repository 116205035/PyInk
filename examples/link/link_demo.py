"""Link example — clickable OSC 8 terminal hyperlinks (Phase 2 PR3).

Reference: ink-link's README examples. PyInk's ``Link`` wraps the rendered
text of its children in an OSC 8 hyperlink sequence; terminals that support
the spec (Windows Terminal, iTerm2, kitty, WezTerm, GNOME Terminal, …)
render the label as a clickable link, while simpler terminals just show the
text plain.

This demo mounts three different link styles side by side:

* a default-coloured URL link,
* a blue + underlined link,
* a green ``file://`` link.

Open the rendered frame in a modern terminal to click them.

Run::

    python examples/link/link_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import Link
from pyink.render.keys import Key


def LinkDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        return Box(
            Text("Link demo", bold=True),
            Text("OSC 8 hyperlinks — click in a modern terminal", dimColor=True),
            Box(
                Text("URL: ", dimColor=True),
                Link(
                    "pyink on GitHub",
                    url="https://github.com/anthropic/pyink",
                ),
                flexDirection="row",
            ),
            Box(
                Text("Sty: ", dimColor=True),
                Link(
                    "blue + underline",
                    url="https://example.com",
                    color="blue",
                    underline=True,
                ),
                flexDirection="row",
            ),
            Box(
                Text("FS:  ", dimColor=True),
                Link(
                    "file:///etc/hostname",
                    url="file:///etc/hostname",
                    color="green",
                ),
                flexDirection="row",
            ),
            Text("Esc / Ctrl+C to quit", dimColor=True),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(LinkDemo(), columns=60, rows=10)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
