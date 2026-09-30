"""The shared renderer: one process drawing for every wrapped window of a user.

A wrapper connects, says which flower it grows, and from then on sends one
tick per frame; this process runs that window's `LocalDrawing` and replies
with the graphics bytes it wrote. What every window would otherwise hold its
own copy of - Pillow, the decoded artwork, the audio device - is held here
once. See `docs/shared-renderer.md`.

Started by `shared._spawn` as `python -m flower_cursor.daemon DIRECTORY TAG`.
It forks at once so the window that started it has no child to reap, holds
an exclusive lock for as long as it listens so two windows opening together
start one renderer between them, and exits once no window has been connected
for `IDLE_EXIT` seconds.
"""

from __future__ import annotations

import fcntl
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

from . import wire
from .drawing import LocalDrawing, Recipe
from .lifecycle import CursorCell
from .shared import PROTOCOL

IDLE_EXIT = 60.0
# Longer than a renderer takes to leave once it has decided to.
LOCK_WAIT = 3.0


class _Shared:
  """What every connected window draws with."""

  def __init__(self) -> None:
    from .mixer import Mixer

    self.mixer = Mixer()
    self._images: dict[tuple[Path, str | None], dict] = {}
    self._lock = threading.Lock()
    self.clients = 0
    self.idle_since = time.monotonic()

  def images_for(self, manifest: Path, species: str | None) -> dict:
    """One species' decoded layers, shared by every window that grows it.

    A window under `cycle` or `random` asks for each species the first
    time it draws one, so the renderer holds the species on screen
    anywhere, once each.
    """
    with self._lock:
      return self._images.setdefault((manifest, species), {})

  def joined(self) -> None:
    with self._lock:
      self.clients += 1

  def left(self) -> None:
    with self._lock:
      self.clients -= 1
      if self.clients == 0:
        self.idle_since = time.monotonic()

  def idle_for(self) -> float:
    with self._lock:
      return 0.0 if self.clients else time.monotonic() - self.idle_since


def _cell(value) -> CursorCell | None:
  return None if value is None else CursorCell(int(value[0]), int(value[1]))


def _apply(drawing: LocalDrawing, events) -> None:
  for event in events:
    kind = event[0]
    if kind == "move":
      drawing.move_to(CursorCell(int(event[1]), int(event[2])))
    elif kind == "screen":
      drawing.screen_reset(str(event[1]).encode("latin-1"))
    elif kind == "wither":
      drawing.wither()


def serve_window(connection: socket.socket, shared: _Shared) -> None:
  """Draw for one window until it says goodbye or goes away.

  Anything that goes wrong is this window's alone: the connection closes,
  the wrapper falls back to drawing for itself, and every other window
  carries on.
  """
  drawing: LocalDrawing | None = None
  written = bytearray()
  said: list[str] = []
  shared.joined()
  try:
    hello, _ = wire.receive(connection)
    if hello.get("t") != "hello" or hello.get("v") != PROTOCOL:
      wire.send(connection, {"error": "unexpected hello"})
      return
    recipe = Recipe.from_json(hello["recipe"])
    try:
      drawing = LocalDrawing(
        recipe,
        written.extend,
        report=said.append,
        open_device=shared.mixer.channel,
        images=shared.images_for,
      )
    # Whatever stops this window's flower is the window's to hear about, and
    # must not take the renderer down for every other window it serves.
    except Exception as error:  # noqa: BLE001
      wire.send(connection, {"error": f"{type(error).__name__}: {error}"})
      return
    from .graphics_renderer import _ID_SPAN

    # The wrapper cannot see which ids the renderer drew with, and needs
    # them if this process dies with images on its screen.
    first = drawing.renderer._image_base
    wire.send(connection, {"first": first, "last": first + _ID_SPAN - 1, "say": said})
    said.clear()
    while True:
      try:
        message, _ = wire.receive(connection)
      except wire.WireError:
        return
      _apply(drawing, message.get("ev", ()))
      kind = message.get("t")
      if kind == "tick":
        size = message.get("vp")
        drawing.tick(
          float(message["d"]),
          _cell(message.get("cell")),
          None if size is None else (int(size[0]), int(size[1]), int(size[2]), int(size[3])),
          bool(message.get("drawing")),
          tuple(CursorCell(int(c[0]), int(c[1])) for c in message.get("cells", ())),
        )
      elif kind == "close":
        drawing.close()
        drawing = None
      wire.send(
        connection,
        {"wants": drawing is not None and drawing.wants_tick(), "say": said},
        bytes(written),
      )
      written.clear()
      said.clear()
      if drawing is None:
        return
  # One window's failure must not end the renderer for the others.
  except Exception:  # noqa: BLE001
    traceback.print_exc(file=sys.stderr)
    sys.stderr.flush()
  finally:
    if drawing is not None:
      # The window went without saying goodbye; its bee must not keep
      # buzzing. What it drew is its wrapper's to clean up.
      try:
        drawing.close()
      except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
    connection.close()
    shared.left()


def _identity(path: Path) -> tuple[int, int] | None:
  try:
    status = path.stat()
  except OSError:
    return None
  return status.st_dev, status.st_ino


def _listen(path: Path) -> tuple[socket.socket, tuple[int, int] | None]:
  server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  server.bind(str(path))
  os.chmod(path, 0o600)
  server.listen(16)
  server.settimeout(1.0)
  return server, _identity(path)


def serve(directory: Path, tag: str, *, idle_exit: float = IDLE_EXIT) -> int:
  """Listen until nobody has been connected for *idle_exit* seconds."""
  lock = open(directory / f"{tag}.lock", "a+")  # noqa: SIM115 - held for life
  # The holder may be a renderer on its way out that a window reached too
  # late, so wait for it briefly; one that is staying keeps the lock, and
  # this one leaves it to it.
  deadline = time.monotonic() + LOCK_WAIT
  while True:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      break
    except BlockingIOError:
      if time.monotonic() >= deadline:
        lock.close()
        return 0
      time.sleep(0.05)
  lock.seek(0)
  lock.truncate()
  lock.write(f"{os.getpid()}\n")
  lock.flush()
  path = directory / f"{tag}.sock"
  try:
    path.unlink()
  except FileNotFoundError:
    pass
  server, bound = _listen(path)
  shared = _Shared()
  try:
    while True:
      try:
        connection, _ = server.accept()
      except TimeoutError:
        if shared.idle_for() >= idle_exit:
          return 0
        if _identity(path) != bound:
          # Something cleaned the socket out of the temporary
          # directory; without it no new window can reach us, and
          # the lock we hold keeps any other renderer from starting.
          server.close()
          try:
            path.unlink()
          except FileNotFoundError:
            pass
          server, bound = _listen(path)
        continue
      connection.settimeout(None)
      threading.Thread(target=serve_window, args=(connection, shared), name="window", daemon=True).start()
  finally:
    # Unlinked while the lock is still held, so the socket that goes is ours.
    try:
      path.unlink()
    except FileNotFoundError:
      pass
    server.close()
    shared.mixer.close()
    lock.close()


def main(argv: list[str] | None = None) -> int:
  directory, tag = (sys.argv[1:] if argv is None else argv)[:2]
  if os.fork():
    os._exit(0)
  os.chdir(directory)
  return serve(Path(directory), tag)


if __name__ == "__main__":
  raise SystemExit(main())
