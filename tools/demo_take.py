"""Record one take of the README demo: a fresh Kitty, the real observer, scripted keys.

Opens a new Kitty instance with the user's own kitty.conf (so the real
flower-cursor shell, theme and font) plus the demo's overrides, drives it
over remote control, and records the display with ffmpeg at its full pixel
resolution, so the terminal running this needs Screen Recording access.
(cua-driver records only one pixel per point.) Every other app is hidden for
the take and shown again after it, the window is centered, and the video is a
16:9 frame of the window on the wallpaper.
The recording has no audio, so the observer writes the buzz it synthesizes to
a tape, which is laid under the video by its timestamps. The scene opens a
short note on the Japanese anemone in nvim (from the shell's autosuggestion,
so the command must be in its history), finishes its last sentence, hops
along that line through all seven flowers, then keeps still until the bee
lands and shoos it with a jump to the end of the line. The bee is gated to
that one visit and runs at its nominal timing, so the shoo lands a set time
after it.
The window opens in the home folder, where the note is written for the take
and removed after it; nothing is saved (nvim exits with :q!), and the
instance quits after the take.
With --zoom, it also renders a second cut with eased close-ups on the
blooms, timed from when the scene's beats happened (kept, with the raw
recording, in its temporary folder).

    uv run tools/demo_take.py                 # record a take
    uv run tools/demo_take.py --dry-run       # rehearse without recording
"""

from __future__ import annotations

import argparse
import json
import random
import re
import signal
import struct
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from PIL import Image

KITTY = "/Applications/kitty.app/Contents/MacOS/kitty"
# The buzz's sample rate, from flower_cursor.buzz.
SAMPLE_RATE = 22050
NOTE_NAME = "japanese-anemone.md"
# From Wikipedia's Eriocapitella hupehensis. One line per paragraph: nvim wraps.
NOTE = """# Japanese anemone

*Eriocapitella hupehensis*, often still called *Anemone hupehensis*, is one of the flowers grouped as Japanese anemones.

It is native to China, Taiwan, Nepal, and Myanmar. Hundreds of years ago a semi-double form escaped cultivation and spread to Japan and Korea, where it became *E. japonica*.

Its Chinese name, 打破碗花花, means "broken bowl flower".

It flowers from July to October, in
"""
# Typed onto the note's unfinished last line, which has a blank line above it
# so every flower on it is drawn whole, clear of the text and the status bars.
TYPED = " purple, pink, or white."

# CGWindowList gives window bounds without Accessibility access: the window
# number, then x y width height in points from the main display's top left.
WINDOW_SWIFT = """
import CoreGraphics
let pid = Int(CommandLine.arguments[1])!
let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as! [[String: Any]]
for w in list where (w[kCGWindowOwnerPID as String] as? Int) == pid
    && (w[kCGWindowLayer as String] as? Int) == 0 {
  let b = w[kCGWindowBounds as String] as! [String: Double]
  if b["Width"]! > 100 && b["Height"]! > 100 { print(w[kCGWindowNumber as String]!, b["X"]!, b["Y"]!, b["Width"]!, b["Height"]!); break }
}
"""
CAPTURE_SWIFT = "import CoreGraphics; print(CGPreflightScreenCaptureAccess())"
DISPLAY_SWIFT = "import CoreGraphics; let b = CGDisplayBounds(CGMainDisplayID()); print(b.width, b.height)"
# Moves the pointer to the given point without clicking.
POINTER_SWIFT = """
import CoreGraphics
let a = CommandLine.arguments
CGWarpMouseCursorPosition(CGPoint(x: Double(a[1])!, y: Double(a[2])!))
"""
# Hides every visible app but the given pid and prints the pids it hid; given
# `show` and pids instead, shows those again.
HIDE_SWIFT = """
import AppKit
let args = CommandLine.arguments.dropFirst()
if args.first == "show" {
  for pid in args.dropFirst() { NSRunningApplication(processIdentifier: pid_t(pid)!)?.unhide() }
} else {
  let keep = pid_t(args.first!)!
  for app in NSWorkspace.shared.runningApplications
      where app.activationPolicy == .regular && !app.isHidden && app.processIdentifier != keep {
    if app.hide() { print(app.processIdentifier) }
  }
  NSRunningApplication(processIdentifier: keep)?.activate()
}
"""


class Take:
  def __init__(self, socket: str, seed: int | None) -> None:
    self.socket = socket
    self.random = random.Random(seed)

  def remote(self, *args: str) -> str:
    return subprocess.run(
      ["kitten", "@", "--to", f"unix:{self.socket}", *args],
      check=True,
      capture_output=True,
      text=True,
    ).stdout

  def send(self, text: str) -> None:
    self.remote("send-text", text)

  def type(self, text: str, cps: float = 11.0) -> None:
    """Type like a person: jittered keystrokes, a beat after spaces and punctuation."""
    deadline = time.monotonic()
    for char in text:
      self.send(char)
      gap = self.random.gauss(1 / cps, 0.35 / cps)
      if char == " ":
        gap += self.random.uniform(0.02, 0.08)
      elif char in ",.":
        gap += self.random.uniform(0.15, 0.3)
      deadline += max(0.03, gap)
      time.sleep(max(0.0, deadline - time.monotonic()))

  def key(self, text: str, then: float) -> None:
    self.send(text)
    time.sleep(then)

  def wait_for_shell(self, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      try:
        windows = json.loads(self.remote("ls"))
        procs = windows[0]["tabs"][0]["windows"][0]["foreground_processes"]
        if any("flower-cursor" in " ".join(p["cmdline"]) for p in procs):
          return
      except subprocess.CalledProcessError, json.JSONDecodeError, IndexError, KeyError:
        pass
      time.sleep(0.3)
    sys.exit("demo Kitty never showed a flower-cursor shell")


# The catalog's flower after the Icelandic poppy, which the bee should visit.
VISITED_AFTER = "dahlia"
# IdleBee's nominal visit: its feet touch the flower this long after it sets
# off, and its wings are folded by this.
TOUCHDOWN = 3.45
SETTLED = 3.8


def scene(take: Take, gate: Path, cue: Path, interval: float, shoo: float) -> dict[str, float]:
  """Play the scene; returns when its beats happened, by the wall clock."""
  # The shell's autosuggestion offers the rest from the last take, so a few
  # keys and a right arrow open the note. `+` opens on the last line, so no
  # bloom lands under the top status bar.
  take.type("nvim")
  time.sleep(0.5)
  if f"nvim + {NOTE_NAME}" not in take.remote("get-text"):
    sys.exit(f"the shell did not suggest `nvim + {NOTE_NAME}`; run it once in a shell and try again")
  take.remote("send-key", "right")
  time.sleep(0.4)
  # Nvim takes about half a second to open; then a beat to look before typing.
  take.key("\r", 1.0)
  # For this buffer only: wrap at word boundaries, and no completion menu over
  # the line being typed. Sent in one go, so nvim does not stop to draw it.
  take.key(":setlocal linebreak | let b:completion = v:false\r", 1.0)
  take.key("A", 0.6)
  take.type(TYPED)
  take.key("\x1b", 1.0)
  # Seven blooms in a row: `cycle` grows the next species at each, so all seven.
  # `W` steps over the punctuation, which spreads them along the line.
  jumps = ("0", "W", "W", "W", "W", "W", "W")
  # Typing buds a flower at every key, too many to count, so the cycle is cued
  # here to bloom the flower after the poppy first: the last jump is then the
  # poppy, which the bee visits.
  cue.write_text(VISITED_AFTER)
  beats = {"jumps": time.time()}
  for jump in jumps[:-1]:
    take.key(jump, take.random.uniform(0.9, 1.1))
  # The bee comes `interval` after the last move, and only while the gate is
  # open, so it cannot visit the prompt before the scene or come back after.
  gate.touch()
  beats["last jump"] = time.time()
  beats["touchdown"] = beats["last jump"] + interval + TOUCHDOWN
  take.key(jumps[-1], interval + SETTLED + shoo)
  beats["shoo"] = time.time()
  take.key("$", 0.0)
  gate.unlink()
  # Long enough for the bee to clear the frame and the shot to settle.
  time.sleep(4.6)
  return beats


def swift(source: str, *args: str) -> str:
  with tempfile.NamedTemporaryFile("w", suffix=".swift") as script:
    script.write(source)
    script.flush()
    return subprocess.run(["swift", script.name, *args], capture_output=True, text=True, check=False).stdout.strip()


def window(pid: int) -> tuple[int, tuple[float, ...]]:
  """The window's number and its bounds."""
  for _ in range(10):
    found = swift(WINDOW_SWIFT, str(pid))
    if found:
      number, *bounds = found.split()
      return int(number), tuple(float(value) for value in bounds)
    time.sleep(0.5)
  sys.exit("could not find the demo Kitty's window")


def display() -> tuple[float, float]:
  width, height = swift(DISPLAY_SWIFT).split()
  return float(width), float(height)


@contextmanager
def alone(pid: int):
  """Hide every other app for the length of the block, then show them again."""
  hidden = swift(HIDE_SWIFT, str(pid)).split()
  try:
    yield
  finally:
    swift(HIDE_SWIFT, "show", *hidden)


def frame(bounds: tuple[float, ...], screen: tuple[float, float]) -> tuple[float, ...]:
  """A 16:9 region around the window with room for the wallpaper, kept on the display."""
  x, y, width, height = bounds
  tall = min(screen[1], height * 1.5, screen[0] * 9 / 16)
  wide = tall * 16 / 9
  left = min(max(0.0, x + width / 2 - wide / 2), screen[0] - wide)
  top = min(max(0.0, y + height / 2 - tall / 2), screen[1] - tall)
  return left, top, wide, tall


class Recorder:
  """A cua-driver MCP session, for its Accessibility grant."""

  def __init__(self) -> None:
    self.process = subprocess.Popen(["cua-driver", "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    self.ids = 0
    self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "demo_take"}})
    self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

  def send(self, message: dict) -> None:
    self.process.stdin.write(json.dumps(message) + "\n")
    self.process.stdin.flush()

  def call(self, method: str, params: dict) -> dict:
    self.ids += 1
    self.send({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params})
    while line := self.process.stdout.readline():
      reply = json.loads(line)
      if reply.get("id") == self.ids:
        if "error" in reply or reply["result"].get("isError"):
          sys.exit(f"cua-driver {method} failed: {reply}")
        return reply["result"]
    sys.exit("cua-driver closed its session")

  def tool(self, name: str, **arguments: object) -> None:
    self.call("tools/call", {"name": name, "arguments": arguments})

  def close(self) -> None:
    self.process.stdin.close()
    self.process.wait(timeout=10)


def center(pid: int, screen: tuple[float, float]) -> tuple[float, ...]:
  """Move the window to the middle of the display, through cua-driver's Accessibility grant."""
  number, (_, _, width, height) = window(pid)
  recorder = Recorder()
  x, y = round((screen[0] - width) / 2), round((screen[1] - height) / 2)
  recorder.tool("set_window_frame", pid=pid, window_id=number, x=x, y=y, width=width, height=height)
  recorder.close()
  return window(pid)[1]


@contextmanager
def recording(out: Path):
  """Record the main display, pixel for pixel, to `out` for the length of the block.

  Frames are stamped with the wall clock, so the start ffmpeg reports for its
  input is when the recording began; it yields that.
  """
  log = out.with_suffix(".log")
  with log.open("w") as errors:
    ffmpeg = subprocess.Popen(
      ["ffmpeg", "-hide_banner", "-y", "-f", "avfoundation", "-capture_cursor", "0", "-framerate", "30"]
      + ["-pixel_format", "nv12", "-use_wallclock_as_timestamps", "1", "-i", "Capture screen 0:none"]
      + ["-c:v", "h264_videotoolbox", "-b:v", "60M", str(out)],
      stdin=subprocess.DEVNULL,
      stderr=errors,
    )
    try:
      end = time.monotonic() + 10
      while not (start := re.search(r"start: (\d+\.\d+)", log.read_text())):
        if ffmpeg.poll() is not None or time.monotonic() > end:
          sys.exit(f"ffmpeg did not start recording; see {log}")
        time.sleep(0.1)
      yield float(start[1])
    finally:
      ffmpeg.send_signal(signal.SIGINT)
      ffmpeg.wait(timeout=30)


def soundtrack(tape: Path, start: float, out: Path) -> None:
  """Lay the taped buzz blocks out from `start`, as a WAV at the level they played at.

  The device asks for blocks just ahead of playing them, so a run of blocks is
  placed from the time of its first and laid end to end after that.
  """
  samples = array("h")
  end = start
  data = tape.read_bytes() if tape.exists() else b""
  offset = 0
  while offset < len(data):
    stamp, size = struct.unpack_from("<dI", data, offset)
    offset += struct.calcsize("<dI")
    block = array("h", data[offset : offset + size])
    offset += size
    if stamp > end + 0.1:
      end = stamp
    first = round((end - start) * SAMPLE_RATE)
    end += len(block) / SAMPLE_RATE
    if first < 0:
      block = block[-first:]
      first = 0
    samples.extend([0] * (first - len(samples)))
    samples[first : first + len(block)] = block
  with wave.open(str(out), "wb") as file:
    file.setnchannels(1)
    file.setsampwidth(2)
    file.setframerate(SAMPLE_RATE)
    file.writeframes(samples.tobytes())


def pixels(raw: Path, bounds: tuple[float, ...]) -> tuple[int, ...]:
  """`bounds`, in points, as even pixels of the display recording."""
  probe = subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0", str(raw)],
    capture_output=True,
    text=True,
    check=True,
  )
  scale = int(probe.stdout) / display()[0]
  return tuple(round(value * scale / 2) * 2 for value in bounds)


# What GitHub and Twitter both play.
ENCODE = ["-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p"]
ENCODE += ["-c:a", "aac", "-b:a", "128k", "-af", "apad", "-shortest", "-movflags", "+faststart"]


def crop(raw: Path, audio: Path, bounds: tuple[float, ...], out: Path) -> None:
  """Cut `bounds` out of the display recording."""
  x, y, width, height = pixels(raw, bounds)
  subprocess.run(
    ["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-i", str(audio), "-vf", f"crop={width}:{height}:{x}:{y}"]
    + ENCODE
    + [str(out)],
    check=True,
  )


# Where each close-up centers, as a fraction of the frame, given the default
# window size and the note: the typed sentence with the blooms above it, and
# the flower the bee visits at the last jump.
SENTENCE = (0.42, 0.453)
VISIT = (0.441, 0.453)
# How far, in frame pixels, the camera's path bows out from a straight line,
# so it swings onto a close-up rather than sliding in.
BOW = 110.0
# The 1080p the close-ups resample to: at 2x that is a third more pixels than
# the frame has, the most before the text visibly softens.
ZOOMED = (1920, 1080)


def smootherstep(x: float) -> float:
  x = min(1.0, max(0.0, x))
  return x * x * x * (x * (x * 6 - 15) + 10)


def zoom(raw: Path, factor: float, out: Path) -> None:
  """Cut the frame like `crop`, but push in on the blooms while the cursor hops and on the bee while it rests.

  The camera eases along an arc onto each close-up and back along another, its
  zoom moving evenly in scale; every frame is resampled from its own sub-pixel
  view of the whole display, so the moves glide rather than step.
  """
  beats = json.loads((raw / "beats.json").read_text())
  start = beats["start"]
  # (ease in from, to, ease out from, to, center), in seconds into the
  # recording. In just before the hops and out once the last bloom has grown;
  # in once the bee has touched down, and out as it is shooed.
  shots = [
    (beats["jumps"] - 1.0, beats["jumps"] - 0.1, beats["last jump"] + 1.3, beats["last jump"] + 2.3, SENTENCE),
    (beats["touchdown"] + 0.1, beats["touchdown"] + 1.1, beats["shoo"], beats["shoo"] + 1.0, VISIT),
  ]
  x, y, width, height = pixels(raw / "screen.mp4", beats["bounds"])
  probe = subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0"]
    + [str(raw / "screen.mp4")],
    capture_output=True,
    text=True,
    check=True,
  )
  screen_width, screen_height = map(int, probe.stdout.split(","))
  wide = (x + width / 2, y + height / 2)

  def view(t: float) -> tuple[float, ...]:
    center, scale = wide, 1.0
    for *times, (fx, fy) in shots:
      a, b, c, d = (beat - start for beat in times)
      if a <= t <= d:
        target = (x + fx * width, y + fy * height)
        # Out of the close-up along the mirror of the arc it came in by.
        k, bow = (smootherstep((t - a) / (b - a)), BOW) if t < c else (1 - smootherstep((t - c) / (d - c)), -BOW)
        dx, dy = target[0] - wide[0], target[1] - wide[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        control = (wide[0] + dx / 2 - dy / length * bow, wide[1] + dy / 2 + dx / length * bow)
        center = tuple((1 - k) ** 2 * p + 2 * (1 - k) * k * q + k * k * r for p, q, r in zip(wide, control, target))
        scale = factor**k
    w, h = width / scale, height / scale
    left = min(max(0.0, center[0] - w / 2), screen_width - w)
    top = min(max(0.0, center[1] - h / 2), screen_height - h)
    return left, top, left + w, top + h

  decode = subprocess.Popen(
    ["ffmpeg", "-v", "error", "-i", str(raw / "screen.mp4"), "-vf", "fps=30"]
    + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
    stdout=subprocess.PIPE,
  )
  encode = subprocess.Popen(
    ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "{}x{}".format(*ZOOMED), "-r", "30"]
    + ["-i", "-", "-i", str(raw / "buzz.wav")]
    + ENCODE
    + [str(out)],
    stdin=subprocess.PIPE,
  )
  size = screen_width * screen_height * 3
  n = 0
  while len(chunk := decode.stdout.read(size)) == size:
    image = Image.frombuffer("RGB", (screen_width, screen_height), chunk)
    encode.stdin.write(image.resize(ZOOMED, Image.Resampling.LANCZOS, box=view(n / 30)).tobytes())
    n += 1
  encode.stdin.close()
  if encode.wait() or decode.wait():
    sys.exit("ffmpeg could not render the close-ups")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  parser.add_argument("--out", type=Path, help="movie path (default: ~/Movies/flower-cursor-take-<time>.mp4)")
  parser.add_argument("--size", default="100c,30c", help="window size in Kitty units (default: 100c,30c)")
  parser.add_argument(
    "--shoo", type=float, default=3.5, help="seconds after the bee settles before it is shooed (default: 3.5)"
  )
  parser.add_argument(
    "--zoom", type=float, default=1.0, help="close-up factor for a second, zoomed cut, such as 2 (default: 1, none)"
  )
  parser.add_argument("--bee", type=float, default=4.0, help="seconds of stillness before the bee comes (default: 4)")
  parser.add_argument("--seed", type=int, help="fix the typing rhythm")
  parser.add_argument("--bee-seed", type=int, default=21, help="the route the bee flies (default: 21)")
  parser.add_argument("--dry-run", action="store_true", help="drive the scene without recording")
  parser.add_argument(
    "--rezoom",
    type=Path,
    metavar="RAW",
    help="only render the zoomed cut again, from a take's raw folder (with --zoom)",
  )
  args = parser.parse_args()

  stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
  if args.rezoom:
    out = args.out or Path.home() / "Movies" / f"flower-cursor-take-{stamp}-zoom.mp4"
    zoom(args.rezoom, args.zoom, out)
    print(out)
    return

  out = args.out or Path.home() / "Movies" / f"flower-cursor-take-{stamp}.mp4"
  socket = f"/tmp/flower-cursor-demo-{stamp}.sock"
  width, height = args.size.split(",")
  note = Path.home() / NOTE_NAME
  if note.exists():
    sys.exit(f"{note} already exists; move it so the take does not overwrite it")
  note.write_text(NOTE)
  raw = Path(tempfile.mkdtemp(prefix="flower-cursor-take-"))
  gate, cue, tape = raw / "gate", raw / "cue", raw / "buzz.tape"

  kitty = subprocess.Popen(
    [
      KITTY,
      "--listen-on",
      f"unix:{socket}",
      "--directory",
      str(Path.home()),
      "-o",
      "allow_remote_control=yes",
      "-o",
      "remember_window_size=no",
      "-o",
      f"initial_window_width={width}",
      "-o",
      f"initial_window_height={height}",
      "-o",
      "env=FLOWER_CURSOR_SPECIES=cycle",
      "-o",
      f"env=FLOWER_CURSOR_BEE={args.bee:g}",
      "-o",
      "env=FLOWER_CURSOR_BUZZ=1",
      "-o",
      # In-process, so the demo's own settings reach what draws the bee.
      "env=FLOWER_CURSOR_DAEMON=off",
      "-o",
      f"env=FLOWER_CURSOR_DEMO_GATE={gate}",
      "-o",
      f"env=FLOWER_CURSOR_DEMO_CUE={cue}",
      "-o",
      f"env=FLOWER_CURSOR_BUZZ_TAPE={tape}",
      "-o",
      f"env=FLOWER_CURSOR_DEMO_SEED={args.bee_seed}",
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
  )
  take = Take(socket, args.seed)
  try:
    take.wait_for_shell()
    time.sleep(1.5)
    take.key("clear\r", 2.0)
    if args.dry_run:
      scene(take, gate, cue, args.bee, args.shoo)
    else:
      if swift(CAPTURE_SWIFT) != "true":
        sys.exit("this terminal needs Screen Recording access: System Settings > Privacy & Security")
      screen = display()
      with alone(kitty.pid):
        bounds = frame(center(kitty.pid, screen), screen)
        # Park the pointer on the right edge, off the frame and clear of hot corners.
        swift(POINTER_SWIFT, str(screen[0] - 4), str(screen[1] / 2))
        with recording(raw / "screen.mp4") as start:
          time.sleep(1.5)
          beats = scene(take, gate, cue, args.bee, args.shoo)
          (raw / "beats.json").write_text(json.dumps({"start": start, "bounds": bounds, **beats}))
  finally:
    try:
      take.key("\x1b:q!\r", 0.8)
    except subprocess.CalledProcessError:
      pass
    # A wrapped window always asks before closing, so end the instance directly.
    kitty.terminate()
    kitty.wait(timeout=10)
    note.unlink(missing_ok=True)
  if not args.dry_run:
    out.parent.mkdir(parents=True, exist_ok=True)
    soundtrack(tape, start, raw / "buzz.wav")
    crop(raw / "screen.mp4", raw / "buzz.wav", bounds, out)
    print(out)
    if args.zoom > 1:
      zoomed = out.with_stem(f"{out.stem}-zoom")
      zoom(raw, args.zoom, zoomed)
      print(zoomed)


if __name__ == "__main__":
  main()
