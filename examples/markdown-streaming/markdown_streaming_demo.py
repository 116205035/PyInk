"""Markdown + StreamingText example — live AI token stream (Phase 3 PR6).

Reference: Claude Code's chat UI. A background thread appends characters
one at a time to a ``Signal[str]``; the ``Markdown`` external accepts
the signal as its source and re-parses on every write, so the user sees
the Markdown render grow live — partial headings, half-finished code
blocks and incomplete tables all render gracefull until the source
completes.

This is the closest of the Phase 3 examples to a real Jarvis / Claude
Code chat surface: AI token stream on one thread, Markdown render on
the main thread.

Optional dependencies: requires both ``markdown-it-py`` and Pygments
(for highlighted code blocks inside the rendered Markdown). Install
with::

    pip install pyink[all]

Partial-source handling: ``markdown-it-py`` parses what it can. An
unclosed fence (```` ``` ```` without a matching close) renders as a
plain code block; an unclosed link renders as literal text. The render
is always graceful — never raises.

Run::

    python examples/markdown-streaming/markdown_streaming_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys
import threading
import time

from pyink import Box, Text, create_element, render, signal, use_app, use_input
from pyink.core.element import Element
from pyink.externals import Markdown
from pyink.render.keys import Key

#: The Markdown source the worker drips into the buffer one character
#: at a time. Picked to span headings, paragraphs, lists and a fenced
#: code block so the user can watch each block type materialise.
SOURCE: str = """\
# Streaming Markdown

This paragraph is being **streamed** token by token from a background
thread. As each character lands, the `Markdown` external re-parses the
buffer and re-renders.

## Live list

* first item
* second item
* third item

## Live code block

```python
def square(n: int) -> int:
    return n * n
```

Done.
"""

#: Inter-character delay (seconds). Small enough that the demo reaches
# completion in a few seconds, large enough that the user can see the
# incremental render.
CHAR_DELAY: float = 0.02


def MarkdownStreamingDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        buffer = signal("")

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def stream_worker() -> None:
            """Background thread: drip ``SOURCE`` into ``buffer``."""
            for ch in SOURCE:
                buffer.value = buffer.value + ch
                time.sleep(CHAR_DELAY)

        threading.Thread(target=stream_worker, daemon=True).start()

        return Box(
            Text("Streaming Markdown demo", bold=True),
            Text(
                "pip install pyink[all]  — Esc / Ctrl+C to quit",
                dimColor=True,
            ),
            Markdown(buffer),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    # ``rows=30`` (was 24) gives the rendered Markdown enough vertical
    # room for the heading + paragraph + list + fenced code block +
    # trailing "Done." paragraph without forcing the flex engine to
    # shrink the inner code-block border Box below its minimum
    # (top-edge + content + bottom-edge). At 24 rows the code block's
    # second source line (``return n * n``) is dropped because the
    # border Box is squeezed to three rows; at 30 both lines fit and
    # the border closes cleanly. See the Phase 3 hardening notes
    # (PRD Bug Fixes → Bug 8) for the renderer-side guard that keeps
    # the border whole even when shrink does leave the box too small.
    inst = render(MarkdownStreamingDemo(), columns=70, rows=30)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
