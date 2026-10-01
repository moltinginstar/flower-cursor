"""The wrapper's side of the shared renderer: find it, start it, talk to it.

One per-user process, `daemon`, holds Pillow, the decoded artwork and the audio
device for every wrapped window. A wrapper sends it the cursor and gets graphics
bytes back.
See `docs/shared-renderer.md` for the split and why.

Nothing here may cost a window its flower. A renderer that cannot be started,
refuses the window, or goes away mid-session leaves the window drawing in its
own process.
"""

from __future__ import annotations

import hashlib
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import wire
from .drawing import Drawing, Recipe
from .lifecycle import CursorCell

# Not a setting: `off` draws in this process and a directory moves the socket.
# The tests use both, and it is how to rule the renderer out when something
# looks wrong.
DAEMON_VARIABLE = "FLOWER_CURSOR_DAEMON"
OFF = "off"
# Long enough for a cold start (an interpreter, Pillow and a species) on a
# slow machine; past it the window draws for itself rather than wait.
STARTUP_TIMEOUT = 5.0
# A tick is one frame; a renderer that has not drawn one in this long is gone.
REPLY_TIMEOUT = 2.0
PROTOCOL = 1
# sun_path is 104 bytes on macOS and 108 on Linux, terminator included.
_SOCKET_PATH_LIMIT = 103

_PACKAGE = Path(__file__).resolve().parent


def build_tag(manifest: Path | None = None) -> str:
  """Name a renderer for exactly this code, interpreter and artwork.

  A window opened after an upgrade must not be drawn by the renderer the
  old code started, so the socket's name is a hash of both; the old one
  exits once the last of its own windows has closed. Artwork counts too:
  the renderer keeps what it decoded for as long as it runs, so rebuilt
  assets need a new one. Every asset build ends by replacing its
  `manifest.json`, so the manifests' identities stand for the images
  beside them without reading thousands of files at every window's open.
  """
  digest = hashlib.sha256()
  digest.update(f"{PROTOCOL}\0{sys.executable}\0{sys.version}\0".encode())
  for path in sorted(_PACKAGE.glob("*.py")):
    digest.update(path.name.encode() + b"\0" + path.read_bytes())
  manifests = set(_PACKAGE.glob("assets/*/manifest.json"))
  if manifest is not None:
    manifests.add(manifest)
  for path in sorted(manifests):
    try:
      status = path.stat()
    except OSError:
      continue
    identity = (status.st_ino, status.st_mtime_ns, status.st_size)
    digest.update(f"{path}\0{identity}\0".encode())
  return digest.hexdigest()[:16]


def runtime_directory(environ=os.environ) -> Path | None:
  """Where this user's renderers listen, or None to draw in-process."""
  chosen = environ.get(DAEMON_VARIABLE, "").strip()
  if chosen.lower() == OFF:
    return None
  if chosen:
    return Path(chosen)
  return Path(tempfile.gettempdir()) / f"flower-cursor-{os.getuid()}"


def _private_directory(directory: Path) -> bool:
  """Make the directory if needed; refuse one another user could write to."""
  try:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    status = directory.lstat()
  except OSError:
    return False
  return (
    stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid() and not status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
  )


def _spawn(directory: Path, tag: str) -> None:
  """Start a renderer detached from this window: its own session, no terminal."""
  log = open(directory / f"{tag}.log", "ab")  # noqa: SIM115 - handed to the child
  try:
    launcher = subprocess.Popen(
      [sys.executable, "-m", "flower_cursor.daemon", str(directory), tag],
      stdin=subprocess.DEVNULL,
      stdout=subprocess.DEVNULL,
      stderr=log,
      cwd=directory,
      start_new_session=True,
      close_fds=True,
    )
  finally:
    log.close()
  # The launcher forks the renderer and exits at once, so the renderer is
  # nobody's child and this window cannot leave a zombie behind.
  if launcher.wait(timeout=STARTUP_TIMEOUT) != 0:
    # It failed before it could fork (an import error, most likely), and
    # waiting out the startup timeout for it would only delay the window.
    raise subprocess.SubprocessError(f"the renderer exited with {launcher.returncode}")


def connect(
  recipe: Recipe,
  *,
  directory: Path | None,
  tag: str | None = None,
  timeout: float = STARTUP_TIMEOUT,
) -> tuple[socket.socket, dict] | None:
  """A connection the renderer has accepted this window on, or None."""
  if directory is None or not _private_directory(directory):
    return None
  tag = build_tag(Path(recipe.manifest)) if tag is None else tag
  path = str(directory / f"{tag}.sock")
  if len(os.fsencode(path)) > _SOCKET_PATH_LIMIT:
    return None
  deadline = time.monotonic() + timeout
  spawned = False
  while True:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
      connection.settimeout(max(0.1, deadline - time.monotonic()))
      connection.connect(path)
      wire.send(connection, {"t": "hello", "v": PROTOCOL, "recipe": recipe.to_json()})
      reply, _ = wire.receive(connection)
    except OSError, wire.WireError:
      connection.close()
      if time.monotonic() >= deadline:
        return None
      if not spawned:
        spawned = True
        try:
          _spawn(directory, tag)
        except OSError, subprocess.SubprocessError:
          return None
      time.sleep(0.02)
      continue
    if "error" in reply:
      connection.close()
      return None
    connection.settimeout(REPLY_TIMEOUT)
    return connection, reply


def delete_range(first: int, last: int) -> bytes:
  """Delete every image with an id in [first, last], and all their placements."""
  return f"\x1b_Ga=d,d=R,x={first},y={last},q=2\x1b\\".encode("ascii")


class RemoteDrawing:
  """`LocalDrawing`'s methods, carried out by the shared renderer.

  Calls between ticks are queued and go with the next one, in order, so the
  renderer sees the same sequence a local drawing would have. If the
  renderer stops answering, its images are deleted by id range and the
  window builds a `LocalDrawing` of its own from *fallback*.
  """

  def __init__(
    self,
    connection: socket.socket,
    hello: dict,
    write: Callable[[bytes], object],
    *,
    report: Callable[[str], None],
    fallback: Callable[[], Drawing],
  ) -> None:
    self._connection: socket.socket | None = connection
    self._images = (int(hello["first"]), int(hello["last"]))
    self._write = write
    self._report = report
    self._fallback = fallback
    self._events: list[list] = []
    self._wants = False
    self.local: Drawing | None = None
    self.closed = False
    # Said while the renderer built this window's drawing: before the child
    # starts, so it reaches the terminal like any startup message.
    for message in hello.get("say", ()):
      report(str(message))

  def move_to(self, cell: CursorCell) -> None:
    if self.local is not None:
      self.local.move_to(cell)
    else:
      self._events.append(["move", cell.row, cell.column])

  def screen_reset(self, sequence: bytes) -> None:
    if self.local is not None:
      self.local.screen_reset(sequence)
    else:
      self._events.append(["screen", sequence.decode("latin-1")])

  def wither(self) -> None:
    if self.local is not None:
      self.local.wither()
    else:
      self._events.append(["wither"])

  def tick(
    self,
    duration: float,
    cell: CursorCell | None,
    window_size: tuple[int, int, int, int] | None,
    drawing: bool,
    cells: tuple[CursorCell, ...],
  ) -> None:
    if self.local is None:
      reply = self._ask(
        {
          "t": "tick",
          "d": duration,
          "cell": None if cell is None else [cell.row, cell.column],
          "vp": None if window_size is None else list(window_size),
          "drawing": drawing,
          "cells": [[c.row, c.column] for c in cells],
        }
      )
      if reply is not None:
        self._wants = bool(reply.get("wants"))
        return
    if self.local is not None:
      self.local.tick(duration, cell, window_size, drawing, cells)

  def wants_tick(self) -> bool:
    if self.local is not None:
      return self.local.wants_tick()
    return self._wants or bool(self._events)

  def close(self) -> None:
    if self.closed:
      return
    self.closed = True
    if self.local is not None:
      self.local.close()
      return
    if self._ask({"t": "close"}, fall_back=False) is None:
      return
    if self._connection is not None:
      self._connection.close()
      self._connection = None

  def _ask(self, header: dict, *, fall_back: bool = True) -> dict | None:
    """Send the queued events with *header*; write what comes back."""
    header["ev"], self._events = self._events, []
    try:
      assert self._connection is not None
      wire.send(self._connection, header)
      reply, payload = wire.receive(self._connection)
    except OSError, wire.WireError, AssertionError:
      self._lose(header["ev"], fall_back=fall_back)
      return None
    if payload:
      self._write(payload)
    for message in reply.get("say", ()):
      self._report(str(message))
    return reply

  def _lose(self, events: list[list], *, fall_back: bool) -> None:
    if self._connection is not None:
      self._connection.close()
      self._connection = None
    # Whatever the renderer last drew is still on screen, and only this
    # window knows the ids it drew with.
    self._write(delete_range(*self._images))
    if not fall_back:
      return
    try:
      self.local = self._fallback()
    # Last resort: the shell outlives any flower that cannot be grown.
    except Exception as error:  # noqa: BLE001
      self._report(f"flower-cursor: the shared renderer went away and no flower could be grown here ({error})")
      self.local = _Inert()
      return
    for event in events:
      if event[0] == "move":
        self.local.move_to(CursorCell(event[1], event[2]))
      elif event[0] == "screen":
        self.local.screen_reset(event[1].encode("latin-1"))
      elif event[0] == "wither":
        self.local.wither()


class _Inert:
  """A drawing that draws nothing: what is left when nothing else could be built."""

  def move_to(self, cell: CursorCell) -> None:
    pass

  def screen_reset(self, sequence: bytes) -> None:
    pass

  def wither(self) -> None:
    pass

  def tick(
    self,
    duration: float,
    cell: CursorCell | None,
    window_size: tuple[int, int, int, int] | None,
    drawing: bool,
    cells: tuple[CursorCell, ...],
  ) -> None:
    pass

  def wants_tick(self) -> bool:
    return False

  def close(self) -> None:
    pass
