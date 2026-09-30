"""Record one take of the README demo: a fresh Kitty, the real observer, scripted keys.

Opens a new Kitty instance with the user's own kitty.conf (so the real
flower-cursor shell, theme and font) plus the two demo overrides, drives it
over remote control, and records that window with system audio so the buzz
is in the take. The scene retypes the README's tagline in nvim, jumps around,
then keeps still so the bee lands and leaves. Nothing is saved: nvim exits
with :q! and the instance quits after the take.

    python3 tools/demo_take.py                # record a take
    python3 tools/demo_take.py --dry-run      # rehearse without recording
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KITTY = "/Applications/kitty.app/Contents/MacOS/kitty"
TAGLINE = "A living flower that grows where your cursor is, in Kitty."

# CGWindowList gives window ids and bounds without Accessibility access.
WINDOW_SWIFT = """
import CoreGraphics
let pid = Int(CommandLine.arguments[1])!
let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as! [[String: Any]]
for w in list where (w[kCGWindowOwnerPID as String] as? Int) == pid
    && (w[kCGWindowLayer as String] as? Int) == 0 {
  let b = w[kCGWindowBounds as String] as! [String: Double]
  if b["Width"]! > 50 { print(w[kCGWindowNumber as String]!); break }
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


def scene(take: Take, hold: float) -> None:
  take.type("nvim README.md")
  take.key("\r", 1.5)
  take.key("5G", 1.2)
  take.key("o", 0.6)
  take.type(TAGLINE)
  take.key("\x1b", 1.0)
  for jump in ("0", "w", "w", "w", "$", "gg", "G", "5G", "j$"):
    take.key(jump, take.random.uniform(0.55, 0.8))
  # Hands off: sway, then the bee (2–4 s wait at --bee 3), its rest, and its exit.
  time.sleep(hold)


def window_id(pid: int) -> str:
  with tempfile.NamedTemporaryFile("w", suffix=".swift") as script:
    script.write(WINDOW_SWIFT)
    script.flush()
    for _ in range(10):
      found = subprocess.run(["swift", script.name, str(pid)], capture_output=True, text=True).stdout.strip()
      if found:
        return found
      time.sleep(0.5)
  sys.exit("could not find the demo Kitty's window")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  parser.add_argument("--out", type=Path, help="movie path (default: ~/Movies/flower-cursor-take-<time>.mov)")
  parser.add_argument("--size", default="100c,30c", help="window size in Kitty units (default: 100c,30c)")
  parser.add_argument("--hold", type=float, default=20.0, help="seconds kept still at the end for the bee")
  parser.add_argument("--bee", default="3", help="FLOWER_CURSOR_BEE for the take (default: 3)")
  parser.add_argument("--seed", type=int, help="fix the typing rhythm")
  parser.add_argument("--dry-run", action="store_true", help="drive the scene without recording")
  args = parser.parse_args()

  stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
  out = args.out or Path.home() / "Movies" / f"flower-cursor-take-{stamp}.mov"
  socket = f"/tmp/flower-cursor-demo-{stamp}.sock"
  width, height = args.size.split(",")

  kitty = subprocess.Popen(
    [
      KITTY,
      "--listen-on",
      f"unix:{socket}",
      "--directory",
      str(REPO),
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
      f"env=FLOWER_CURSOR_BEE={args.bee}",
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
  )
  take = Take(socket, args.seed)
  recorder = None
  try:
    take.wait_for_shell()
    time.sleep(1.5)
    take.key("clear\r", 2.0)
    if not args.dry_run:
      out.parent.mkdir(parents=True, exist_ok=True)
      recorder = subprocess.Popen(
        ["screencapture", "-v", "-A", "-x", f"-l{window_id(kitty.pid)}", str(out)],
        stdin=subprocess.DEVNULL,
      )
      time.sleep(1.5)
    scene(take, args.hold)
  finally:
    if recorder:
      recorder.send_signal(signal.SIGINT)
      recorder.wait(timeout=30)
    try:
      take.key("\x1b:q!\r", 0.8)
    except subprocess.CalledProcessError:
      pass
    # A wrapped window always asks before closing, so end the instance directly.
    kitty.terminate()
    kitty.wait(timeout=10)
  if recorder:
    print(out)


if __name__ == "__main__":
  main()
