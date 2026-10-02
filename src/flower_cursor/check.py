"""Report whether this machine is set up to grow flowers.

Nothing here draws anything or talks to the terminal: it inspects the parts an
install is made of (Kitty, the launcher, the flower assets and the settings)
and says what it found. A window that stays flowerless
usually has exactly one of these wrong, and reading it off a list beats
guessing at it from an empty screen.

It can't tell you the flowers will appear: that needs Kitty's graphics protocol
over a real terminal, which a report run into a pipe can't exercise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TextIO

from .buzz import have_miniaudio
from .settings import (
  FLOWER_CATALOG,
  KITTY_SHELL_VARIABLE,
  SETTINGS,
  Settings,
  resolve,
)

LAUNCHER_NAME = "flower-cursor-shell"
# Where install.sh puts a checkout when it was not run from one.
_DEFAULT_INSTALL = Path("~/.local/share/flower-cursor")
# A checkout's own launcher, beside the package rather than inside it. A wheel
# installed into site-packages has no such sibling, which is why this is one
# candidate and not the answer.
_CHECKOUT_LAUNCHER = Path(__file__).resolve().parents[2] / "kitty" / LAUNCHER_NAME


def install_directory() -> Path:
  """The directory `install.sh` would keep the checkout in."""
  override = os.environ.get("FLOWER_CURSOR_DIR")
  if override:
    return Path(override)
  data = os.environ.get("XDG_DATA_HOME")
  if data:
    return Path(data) / "flower-cursor"
  return _DEFAULT_INSTALL.expanduser()


def launcher_path() -> Path | None:
  """The launcher Kitty would run, wherever this copy was installed from.

  A checkout keeps it beside the package; `install.sh` clones one into the
  data directory; and a `pip install` has no launcher at all, only the
  `flower-cursor` entry point, which is what the last candidate finds.
  """
  candidates = [
    _CHECKOUT_LAUNCHER,
    install_directory() / "kitty" / LAUNCHER_NAME,
  ]
  for name in (LAUNCHER_NAME, "flower-cursor"):
    found = shutil.which(name)
    if found is not None:
      candidates.append(Path(found))
  return next((candidate for candidate in candidates if candidate.exists()), None)


_OK = "ok"
_WARN = "warn"
_BAD = "bad"
_MARKS = {_OK: "+", _WARN: "!", _BAD: "x"}


def _kitty_version() -> str | None:
  """Kitty's version, or None if it is not installed or will not say.

  Kitty is usually on PATH, but a macOS user who dragged the app into
  /Applications may not have run its `kitty +runpy` linking step, so a
  missing binary is worth reporting without being certain it means absence.
  """
  binary = shutil.which("kitty")
  if binary is None:
    return None
  try:
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10, check=False)
  except OSError, subprocess.SubprocessError:
    return None
  # `kitty 0.49.1 created by Kovid Goyal`
  words = result.stdout.split()
  if len(words) > 1 and words[0] == "kitty":
    return words[1]
  return result.stdout.strip() or None


def _setting_lines(settings: Settings) -> list[tuple[str, str, str]]:
  """What this window was told, and which spelling told it.

  Every row comes from the resolved settings rather than from the raw
  variables, so the report says what a window will actually do rather than
  what a config file appears to ask for: an unreadable value has already
  been replaced by the default, and this says so.
  """
  lines = []
  for setting in SETTINGS:
    if setting.development and settings.sources[setting.name] == "default":
      # Knobs nobody touched are noise in a report about why nothing grew.
      continue
    source = settings.sources[setting.name]
    value = getattr(settings, setting.attribute)
    detail = _value_text(setting.name, value)
    where = "the default" if source == "default" else source
    status = _OK
    if setting.name == "buzz" and settings.buzz > 0:
      if settings.bee is None:
        status, detail = _WARN, f"{detail}, but there is no bee to buzz"
      elif not have_miniaudio():
        status, detail = _WARN, f"{detail}, but the sound extra isn't installed, so the bee is silent"
    lines.append((status, setting.name, f"{detail} (from {where})"))
  if os.environ.get(KITTY_SHELL_VARIABLE):
    lines.append((_OK, KITTY_SHELL_VARIABLE, "set, so Kitty's shell integration is nested"))
  return lines


def _value_text(name: str, value: object) -> str:
  """One setting's value, in the words the setting is about."""
  match name, value:
    case "species", "cycle":
      return "cycle, the next flower at every bloom"
    case "species", "random":
      return "random, another flower at every bloom"
    case "species", _:
      return f"{value}"
    case "bee", None:
      return "no bee visits"
    case "bee", _:
      return f"a bee about every {value:g}s"
    case "buzz", 0.0:
      return "silent"
    case "buzz", _:
      return f"{value:g}"
    case "fps", _:
      return f"{value:g}"
    case "log", None:
      return "not written"
    case _, _:
      return f"{value}"


def report(settings: Settings | None = None) -> tuple[list[tuple[str, str, str]], bool]:
  """Every check, and whether anything in it stops flowers growing."""
  settings = resolve() if settings is None else settings
  lines: list[tuple[str, str, str]] = []

  version = _kitty_version()
  if version is None:
    lines.append((_BAD, "kitty", "not found on PATH"))
  else:
    lines.append((_OK, "kitty", version))
  if os.environ.get("KITTY_WINDOW_ID") is None:
    lines.append((_WARN, "terminal", "this is not a Kitty window, so nothing would draw here"))
  else:
    lines.append((_OK, "terminal", f"Kitty window {os.environ['KITTY_WINDOW_ID']}"))

  lines.append((_OK, "python", sys.version.split()[0]))

  launcher = launcher_path()
  if launcher is None:
    lines.append((_BAD, "launcher", f"no {LAUNCHER_NAME} in a checkout, {install_directory()} or PATH"))
  elif not os.access(launcher, os.X_OK):
    lines.append((_BAD, "launcher", f"{launcher} is not executable"))
  else:
    lines.append((_OK, "launcher", str(launcher)))

  missing = [flower.name for flower in FLOWER_CATALOG if not flower.manifest.exists()]
  if missing:
    lines.append((_BAD, "flowers", f"no assets for {', '.join(missing)}"))
  else:
    lines.append((_OK, "flowers", f"{len(FLOWER_CATALOG)} installed"))

  lines.extend(_setting_lines(settings))
  return lines, any(status == _BAD for status, _, _ in lines)


def run_check(settings: Settings | None = None, *, stream: TextIO | None = None) -> int:
  out = stream if stream is not None else sys.stdout
  lines, broken = report(settings)
  width = max(len(label) for _, label, _ in lines)
  for status, label, detail in lines:
    print(f"{_MARKS[status]} {label.ljust(width)}  {detail}", file=out)
  print(file=out)
  if broken:
    print("Something above is missing; the flower cursor will not run.", file=out)
  else:
    print("Everything the flower cursor needs is here.", file=out)
  return 1 if broken else 0
