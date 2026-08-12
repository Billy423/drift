"""Help output as text, with terminal styling removed.

Rich treats some environments as colour-capable whether or not anything is attached to a
terminal — GitHub Actions among them — so the same command yields plain text on a developer's
machine and escape-wrapped text in continuous integration. A substring like `--budget` is then
split across escape sequences and no longer appears in the output at all.

Every assertion about help is about its wording, never its styling, so styling is stripped
before any of them look at it.
"""

from __future__ import annotations

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Return `text` with SGR escape sequences removed."""
    return _ANSI.sub("", text)
