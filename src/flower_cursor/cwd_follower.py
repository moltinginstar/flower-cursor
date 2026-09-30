"""Keep the wrapper's own working directory on the shell's.

Kitty answers `launch --cwd=current` - which is what `new_window_with_cwd` and
its neighbours use - from the OSC 7 directory the shell reports, but only while
`Window.at_prompt` holds. That test is `screen_cursor_at_a_shell_prompt` in
`screen.c`, and its first condition is `screen_is_cursor_visible`: hiding the
native cursor, which is the whole point of the flower, makes it permanently
false. Kitty then falls back to the working directory of the foreground
process - this wrapper - so new windows would open wherever the wrapper was
started rather than where the user is.

Following the shell's own OSC 7 reports with `chdir` puts the right answer in
the place Kitty actually reads.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

# OSC 7 ; <url> terminated by BEL or ST. Kitty accepts either terminator.
_OSC7 = re.compile(rb"\x1b\]7;([^\x07\x1b]*)(?:\x07|\x1b\\)")
# An OSC 7 payload is a single path; nothing legitimate approaches this, and a
# stream without a terminator must not grow the buffer without bound.
_MAX_PENDING = 4096


class CwdFollower:
  """Report the directory the child last announced with OSC 7."""

  def __init__(self) -> None:
    self._tail = b""
    self._current: str | None = None

  def feed(self, data: bytes) -> str | None:
    """Return the newly reported directory, or None if it is unchanged."""
    combined = self._tail + data
    path: str | None = None
    for match in _OSC7.finditer(combined):
      path = _path_from_url(match.group(1)) or path
    self._tail = combined[-_MAX_PENDING:]
    if path is None or path == self._current:
      return None
    self._current = path
    return path


def _path_from_url(raw: bytes) -> str | None:
  try:
    url = raw.decode("utf-8")
  except UnicodeDecodeError:
    return None
  parts = urlsplit(url)
  if parts.scheme and parts.scheme != "file":
    return None
  # A hostname that is not ours means the shell is somewhere else entirely -
  # over ssh, say - and its path is meaningless here. Kitty makes the same
  # distinction with `child_is_remote`.
  if parts.hostname and not _is_local(parts.hostname):
    return None
  return unquote(parts.path) or None


def _is_local(hostname: str) -> bool:
  import socket

  if hostname in {"localhost", "127.0.0.1", "::1"}:
    return True
  return hostname.split(".")[0].lower() == socket.gethostname().split(".")[0].lower()
