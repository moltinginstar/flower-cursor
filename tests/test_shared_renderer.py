"""The shared renderer: one process drawing, and its audio, for many windows."""

import fcntl
import os
import pty
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from array import array
from pathlib import Path

import pytest

from flower_cursor import daemon, shared, wire
from flower_cursor.drawing import LocalDrawing, Recipe
from flower_cursor.lifecycle import CursorCell
from flower_cursor.mixer import Mixer
from flower_cursor.observer import DEFAULT_MANIFEST
from flower_cursor.screen_resets import ScreenResets

RECIPE = Recipe(
  flowers=((str(DEFAULT_MANIFEST), None),),
  order="fixed",
  bee=None,
  unique_flowers=4,
  volume=0.0,
)
SIZE = (30, 100, 1600, 990)


def test_a_message_survives_the_wire_whole():
  left, right = socket.socketpair()
  with left, right:
    wire.send(left, {"t": "tick", "cells": [[1, 2]]}, b"\x1b_Gpixels")
    assert wire.receive(right) == ({"t": "tick", "cells": [[1, 2]]}, b"\x1b_Gpixels")
    left.close()
    with pytest.raises(wire.WireError):
      wire.receive(right)


@pytest.mark.parametrize(
  ("chunks", "expected"),
  [
    ([b"hello\x1b[2Jworld"], [b"\x1b[2J"]),
    ([b"\x1b[?10", b"49h"], [b"\x1b[?1049h"]),
    ([b"\x1b[?25l\x1b[?1;47h"], [b"\x1b[?1;47h"]),
    ([b"\x1bc", b"\x1b[3J\x1b[22J"], [b"\x1bc", b"\x1b[3J", b"\x1b[22J"]),
    ([b"\x1b[J\x1b[1J\x1b[!p"], []),
  ],
)
def test_the_wrapper_sees_the_same_resets_the_renderer_does(chunks, expected):
  resets = ScreenResets()
  found = [sequence for chunk in chunks for sequence in resets.feed(chunk)]
  assert found == expected


class FakeDevice:
  def __init__(self, sample_rate):
    self.sample_rate = sample_rate
    self.events = []
    self.stream = None

  def start(self, stream):
    self.events.append("start")
    self.stream = stream

  def stop(self):
    self.events.append("stop")

  def close(self):
    self.events.append("close")


def tone(value):
  frames = yield b""
  while True:
    frames = yield array("h", [value] * frames).tobytes()


def primed(generator):
  next(generator)
  return generator


def test_one_device_plays_every_window_and_stops_when_they_all_do():
  opened = []
  mixer = Mixer(open_device=lambda rate: opened.append(FakeDevice(rate)) or opened[-1])
  first, second = mixer.channel(mixer.sample_rate), mixer.channel(mixer.sample_rate)
  assert len(opened) == 1
  device = opened[0]
  first.start(primed(tone(1000)))
  second.start(primed(tone(30000)))
  assert device.events == ["start"]
  assert list(array("h", device.stream.send(3))) == [31000] * 3
  second.stop()
  assert list(array("h", device.stream.send(2))) == [1000, 1000]
  first.stop()
  assert device.events == ["start", "stop"]
  first.start(primed(tone(5)))
  assert device.events == ["start", "stop", "start"]
  mixer.close()
  assert device.events == ["start", "stop", "start", "stop", "close"]


def test_a_mixer_that_cannot_open_says_so_to_the_voice_that_asked():
  def refuse(rate):
    raise RuntimeError("no card")

  with pytest.raises(RuntimeError, match="no card"):
    Mixer(open_device=refuse).channel(22050)


def test_two_loud_voices_clip_instead_of_wrapping():
  opened = []
  mixer = Mixer(open_device=lambda rate: opened.append(FakeDevice(rate)) or opened[-1])
  mixer.channel(mixer.sample_rate).start(primed(tone(30000)))
  mixer.channel(mixer.sample_rate).start(primed(tone(30000)))
  assert list(array("h", opened[0].stream.send(2))) == [32767, 32767]


@pytest.fixture
def renderer():
  """A renderer listening in this process, and a way to reach it."""
  directory = Path(tempfile.mkdtemp(prefix="fc-", dir="/tmp"))
  exited = []
  thread = threading.Thread(target=lambda: exited.append(daemon.serve(directory, "test", idle_exit=0.5)), daemon=True)
  thread.start()
  deadline = time.monotonic() + 5
  while not (directory / "test.sock").exists():
    assert time.monotonic() < deadline
    time.sleep(0.01)
  yield directory
  thread.join(timeout=10)
  shutil.rmtree(directory)
  assert exited == [0], "the renderer did not leave once its windows had"


def remote(directory, written, reported=None, fallback=None):
  accepted = shared.connect(RECIPE, directory=directory, tag="test", timeout=5)
  assert accepted is not None
  return shared.RemoteDrawing(
    *accepted,
    written.extend,
    report=(reported if reported is not None else []).append,
    fallback=fallback or (lambda: pytest.fail("fell back")),
  )


def test_a_window_is_drawn_by_the_renderer_and_cleans_up_through_it(renderer):
  written = bytearray()
  drawing = remote(renderer, written)
  drawing.move_to(CursorCell(5, 10))
  assert drawing.wants_tick(), "a queued move is something to draw"
  for _ in range(20):
    drawing.tick(1 / 60, CursorCell(5, 10), SIZE, True, ())
  assert b"\x1b_G" in written and b"a=T" in written
  assert drawing.wants_tick()
  drawn = len(written)
  drawing.close()
  assert b"a=d" in written[drawn:]
  drawing.close()


def test_the_renderer_draws_what_this_process_would_have(renderer):
  """Frame for frame the same bytes, image ids aside."""
  import re

  written = bytearray()
  drawing = remote(renderer, written)
  here = bytearray()
  local = LocalDrawing(RECIPE, here.extend, report=print)
  for target in (drawing, local):
    target.move_to(CursorCell(5, 10))
    for _ in range(10):
      target.tick(1 / 60, CursorCell(5, 10), SIZE, True, ())
    target.wither()
    for _ in range(10):
      target.tick(1 / 60, CursorCell(5, 10), SIZE, False, ())
  drawing.close()
  local.close()

  def without_ids(data):
    return re.sub(rb"i=\d+", b"i=?", bytes(data))

  assert without_ids(written) == without_ids(here)


def test_a_renderer_that_goes_away_leaves_the_window_drawing_for_itself(renderer):
  written = bytearray()
  built = []

  def fallback():
    built.append(LocalDrawing(RECIPE, written.extend, report=print))
    return built[-1]

  drawing = remote(renderer, written, fallback=fallback)
  drawing.tick(1 / 60, CursorCell(5, 10), SIZE, True, ())
  first, last = drawing._images
  # The renderer's end of this window's connection closes under it.
  drawing._connection.shutdown(socket.SHUT_RDWR)
  drawing.move_to(CursorCell(6, 10))
  drawing.tick(1 / 60, CursorCell(6, 10), SIZE, True, ())
  assert shared.delete_range(first, last) in written
  assert built and drawing.local is built[0]
  assert built[0].runtime.active is not None, "the queued move was not replayed"
  drawing.close()


def test_a_window_the_renderer_cannot_draw_is_refused_not_hung(renderer):
  broken = Recipe(
    flowers=(("/nonexistent/manifest.json", None),),
    order="fixed",
    bee=None,
    unique_flowers=1,
    volume=0.0,
  )
  assert shared.connect(broken, directory=renderer, tag="test", timeout=2) is None


def test_a_directory_others_can_write_to_is_not_used(tmp_path):
  directory = tmp_path / "open"
  directory.mkdir()
  directory.chmod(0o777)
  assert shared.connect(RECIPE, directory=directory, tag="test", timeout=1) is None
  assert list(directory.iterdir()) == []


def test_off_means_in_process(monkeypatch):
  assert shared.runtime_directory({"FLOWER_CURSOR_DAEMON": "off"}) is None
  assert shared.runtime_directory({"FLOWER_CURSOR_DAEMON": "/x"}) == Path("/x")
  default = shared.runtime_directory({})
  assert default is not None and default.name == f"flower-cursor-{os.getuid()}"


def test_the_tag_changes_with_the_code(monkeypatch, tmp_path):
  package = tmp_path / "package"
  package.mkdir()
  for path in shared._PACKAGE.glob("*.py"):
    (package / path.name).write_bytes(path.read_bytes())
  monkeypatch.setattr(shared, "_PACKAGE", package)
  before = shared.build_tag()
  assert shared.build_tag() == before
  (package / "observer.py").write_text("# changed\n")
  assert shared.build_tag() != before


# Two real windows, one real renderer, and a stand-in for the sound extra.
_CHILD = """
import fcntl, os, select, struct, termios, time, tty
tty.setraw(0)
deadline = time.monotonic() + 8
while time.monotonic() < deadline:
    if select.select([0], [], [], 0.1)[0] and b'q' in os.read(0, 256):
        break
"""
_RUNNER = f"""
import sys
import tempfile
from flower_cursor.observer import main
argv = ['--buzz', '1', '--bee', '0.05', '--', sys.executable, '-c', {_CHILD!r}]
raise SystemExit(main(argv))
"""
_STUB = """
import enum, os, pathlib


class SampleFormat(enum.Enum):
    SIGNED16 = 2


class PlaybackDevice:
    def __init__(self, output_format, nchannels, sample_rate):
        self.log = pathlib.Path("device.log")
        with self.log.open("a") as handle:
            handle.write(f"opened {os.getpid()}\\n")

    def start(self, stream):
        with self.log.open("a") as handle:
            handle.write(f"frames {len(stream.send(480))}\\n")

    def stop(self):
        with self.log.open("a") as handle:
            handle.write("stopped\\n")

    def close(self):
        pass
"""


def _window(tmp_path, environment):
  master, slave = pty.openpty()
  fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", *SIZE))
  process = subprocess.Popen(
    [sys.executable, "-c", _RUNNER],
    stdin=slave,
    stdout=slave,
    stderr=subprocess.PIPE,
    cwd=tmp_path,
    env=environment,
  )
  os.close(slave)
  return process, master


def _renderer_pid(directory):
  locks = list(directory.glob("*.lock"))
  assert len(locks) == 1, locks
  return int(locks[0].read_text().split()[0])


def test_a_socket_path_too_long_to_bind_draws_in_process_at_once(tmp_path):
  directory = tmp_path / ("x" * 100)
  started = time.monotonic()
  assert shared.connect(RECIPE, directory=directory, tag="test", timeout=5) is None
  assert time.monotonic() - started < 1


def test_two_real_windows_share_one_renderer_and_one_audio_device(tmp_path):
  directory = Path(tempfile.mkdtemp(prefix="fc-", dir="/tmp")) / "run"
  stub = tmp_path / "stub"
  stub.mkdir()
  (stub / "miniaudio.py").write_text(_STUB)
  environment = dict(os.environ, PYTHONPATH=str(stub), FLOWER_CURSOR_DAEMON=str(directory))
  windows = [_window(tmp_path, environment) for _ in range(2)]
  outputs = [bytearray() for _ in windows]
  tails = [b""] * len(windows)
  started = time.monotonic()
  stopped = False
  pid = None
  try:
    while time.monotonic() - started < 12:
      masters = [master for process, master in windows if process.poll() is None]
      if not masters:
        break
      readable, _, _ = select.select(masters, [], [], 0.05)
      for index, (process, master) in enumerate(windows):
        if master not in readable:
          continue
        try:
          chunk = os.read(master, 65536)
        except OSError:
          continue
        outputs[index].extend(chunk)
        queries = tails[index] + chunk
        for _ in range(queries.count(b"\x1b[?6n")):
          os.write(master, b"\x1b[?9;13R")
        tails[index] = queries[-4:]
      if pid is None and list(directory.glob("*.lock")):
        pid = _renderer_pid(directory)
      if not stopped and time.monotonic() - started >= 4:
        for _, master in windows:
          os.write(master, b"q")
        stopped = True
    for process, _ in windows:
      assert process.wait(timeout=5) == 0, process.stderr.read().decode()
      assert process.stderr.read() == b""
    for output in outputs:
      # A flower and a bee, drawn by the renderer and written by the window.
      assert b"p=1" in output and b"p=2" in output
    assert pid is not None and pid not in (p.pid for p, _ in windows)
    log = (directory / "device.log").read_text().splitlines()
    assert [line for line in log if line.startswith("opened")] == [f"opened {pid}"]
    assert "frames 960" in log
  finally:
    for process, master in windows:
      if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
      os.close(master)
    if pid is None and directory.exists() and list(directory.glob("*.lock")):
      pid = _renderer_pid(directory)
    if pid is not None:
      try:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
          os.kill(pid, 0)
          time.sleep(0.02)
      except ProcessLookupError:
        pass
    shutil.rmtree(directory.parent, ignore_errors=True)


def test_a_lost_renderer_with_nothing_to_fall_back_on_leaves_the_session_alone(renderer):
  written = bytearray()
  reported = []

  def fallback():
    raise RuntimeError("no Pillow here")

  drawing = remote(renderer, written, reported, fallback=fallback)
  first, last = drawing._images
  drawing._connection.shutdown(socket.SHUT_RDWR)
  drawing.tick(1 / 60, CursorCell(5, 10), SIZE, True, ())
  drawing.tick(1 / 60, CursorCell(5, 10), SIZE, True, ())
  assert shared.delete_range(first, last) in written
  assert isinstance(drawing.local, shared._Inert)
  assert any("no Pillow here" in message for message in reported)
  assert not drawing.wants_tick()
  drawing.close()


def test_rebuilt_artwork_gets_a_renderer_of_its_own(monkeypatch, tmp_path):
  package = tmp_path / "package"
  (package / "assets" / "rose").mkdir(parents=True)
  (package / "observer.py").write_text("# code\n")
  manifest = package / "assets" / "rose" / "manifest.json"
  manifest.write_text("{}\n")
  monkeypatch.setattr(shared, "_PACKAGE", package)
  before = shared.build_tag()
  assert shared.build_tag() == before
  # How an asset build finishes: a new file renamed over the old one.
  replacement = manifest.with_suffix(".tmp")
  replacement.write_text('{"crops": {}}\n')
  replacement.replace(manifest)
  assert shared.build_tag() != before
  own = tmp_path / "mine" / "manifest.json"
  own.parent.mkdir()
  own.write_text("{}\n")
  assert shared.build_tag(own) != shared.build_tag()


def test_a_manifest_named_from_here_is_found_from_the_renderer_too(monkeypatch, tmp_path):
  from types import SimpleNamespace

  monkeypatch.chdir(tmp_path)
  settings = SimpleNamespace(flowers=((Path("mine/manifest.json"), None),), order="fixed", bee=None, unique_flowers=4)
  recipe = Recipe.from_settings(settings, volume=0.0)
  assert recipe.manifest == str(Path.cwd() / "mine" / "manifest.json")


def test_a_renderer_that_cannot_start_is_given_up_on_at_once(monkeypatch):
  directory = Path(tempfile.mkdtemp(prefix="fc-", dir="/tmp"))
  try:
    monkeypatch.setattr(shared.sys, "executable", "/usr/bin/false")
    started = time.monotonic()
    assert shared.connect(RECIPE, directory=directory, tag="test", timeout=5) is None
    assert time.monotonic() - started < 2
  finally:
    shutil.rmtree(directory)


def test_a_renderer_whose_socket_is_cleaned_away_listens_again(monkeypatch, renderer):
  # Only the running renderer may answer; a spawned one would outlive the test.
  monkeypatch.setattr(shared, "_spawn", lambda directory, tag: None)
  written = bytearray()
  staying = remote(renderer, written)
  (renderer / "test.sock").unlink()
  remote(renderer, written).close()
  staying.close()


def test_a_renderer_waits_for_one_that_is_leaving(monkeypatch):
  directory = Path(tempfile.mkdtemp(prefix="fc-", dir="/tmp"))
  try:
    with open(directory / "test.lock", "a+") as leaving:
      fcntl.flock(leaving, fcntl.LOCK_EX)
      exited = []
      thread = threading.Thread(
        target=lambda: exited.append(daemon.serve(directory, "test", idle_exit=0.5)),
        daemon=True,
      )
      thread.start()
      time.sleep(0.5)
      assert not (directory / "test.sock").exists()
    # The old renderer's lock is gone; the new one takes over and serves.
    drawing = remote(directory, bytearray())
    drawing.close()
    thread.join(timeout=10)
    assert exited == [0]
  finally:
    shutil.rmtree(directory)


def test_a_renderer_leaves_a_lock_that_is_kept(monkeypatch):
  directory = Path(tempfile.mkdtemp(prefix="fc-", dir="/tmp"))
  monkeypatch.setattr(daemon, "LOCK_WAIT", 0.3)
  try:
    with open(directory / "test.lock", "a+") as staying:
      fcntl.flock(staying, fcntl.LOCK_EX)
      assert daemon.serve(directory, "test") == 0
      assert not (directory / "test.sock").exists()
  finally:
    shutil.rmtree(directory)
