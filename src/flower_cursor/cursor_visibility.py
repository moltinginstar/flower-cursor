"""Decide who owns the caret: this flower, or an application drawing its own.

Kitty's native cursor stays hidden while flower mode runs, because the stalk is
the caret. That only holds while the application under us agrees there should
be one caret at that cell. A full-screen program that paints its own cursor -
a coding agent's input box, a pager's position bar - first hides the terminal's
with DECTCEM (`CSI ? 25 l`), because otherwise every terminal would show two.
That request is the general signal, and the only one: it is what the
application tells every terminal, not something learned about a particular
program.

So the mode is treated the way mode 1004 already is in `pty_runner`: the child
owns it for itself, we record what it asked for and re-assert our own hide, and
the flower follows the child's intent. Nothing here inspects the child's name.

The one thing that needs care is a caret hidden for a moment rather than for a
session. Hiding the cursor across a redraw is ordinary practice, so ownership
changes only once the caret has been gone longer than a redraw lasts; a caret
that comes back takes ownership again immediately.
"""

from __future__ import annotations

CURSOR_HIDE = b"\x1b[?25l"
CURSOR_SHOW = b"\x1b[?25h"

# Long enough to sit out a redraw's hide/show pair, short enough that handing
# the caret over is not something the user waits for.
HANDOVER_DELAY = 0.15


class NativeCursorPolicy:
    """Whether the application under us has taken the caret over."""

    def __init__(self, delay: float = HANDOVER_DELAY) -> None:
        self._delay = delay
        self._hidden_since: float | None = None
        self.child_draws_caret = False

    def child_set(self, visible: bool, now: float) -> bool:
        """Record a DECTCEM request from the child; True if ownership changed."""
        if not visible:
            if self._hidden_since is None:
                self._hidden_since = now
            return False
        self._hidden_since = None
        if not self.child_draws_caret:
            return False
        self.child_draws_caret = False
        return True

    def poll(self, now: float) -> bool:
        """Let a long enough hide take the caret over; True if it just did."""
        if self.child_draws_caret or self._hidden_since is None:
            return False
        if now - self._hidden_since < self._delay:
            return False
        self.child_draws_caret = True
        return True
