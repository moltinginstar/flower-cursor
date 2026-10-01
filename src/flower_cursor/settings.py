"""What a user can ask of the cursor, and where the flowers it names live.

Every setting is declared once, in `SETTINGS`, and that single declaration is
what builds the command-line flag, the environment variable, the `--help`
line and the `--check` report. Kitty's `shell` option passes no arguments, so
a setting that exists only as a flag cannot be reached from a Kitty config at
all; declaring the two spellings separately is how they drift apart.

Both the wrapper and `--check` need this: one to grow the flower, the other to
say what would grow. Keeping it here is what lets the report describe an
install without importing the wrapper that performs one.
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bee import DEFAULT_INTERVAL

DEFAULT_SPECIES = "anemone"
DEFAULT_MANIFEST = Path(__file__).with_name("assets") / "layered" / "manifest.json"
# One word turns off everything that can be turned off (the bee, the buzz,
# the log), so there is a single thing to learn and a single thing to document.
# Anything else is a value, so a variable left over from some other tool cannot
# quietly empty the screen.
OFF = "off"
# How a window picks its flowers: one named species for good, or a new pick
# for every bud: the next flower in the catalog, or any other one. The two
# words are species values rather than a setting of their own, because they
# answer the same question the species does: which flower grows.
FIXED = "fixed"
CYCLE = "cycle"
RANDOM = "random"
ORDERS = (CYCLE, RANDOM)


@dataclass(frozen=True, slots=True)
class FlowerSpec:
  name: str
  description: str
  manifest: Path


# Name and description; the assets live in a directory named after the species
# beside the default one, so the manifest path is derived rather than spelled
# out per flower. The default is the exception: it was the only flower before
# there were species, and its directory keeps the name it was authored under.
_ASSETS = DEFAULT_MANIFEST.parent.parent
FLOWER_CATALOG = tuple(
  FlowerSpec(
    name,
    description,
    DEFAULT_MANIFEST if name == DEFAULT_SPECIES else _ASSETS / name / "manifest.json",
  )
  for name, description in (
    ("anemone", "Pink Japanese anemone"),
    ("cornflower", "Blue cornflower"),
    ("daisy", "White daisy"),
    ("crocus", "Violet crocus"),
    ("poppy", "Icelandic poppy"),
    ("dahlia", "Ruby-red dahlia"),
    ("orchid", "Green-and-white slipper orchid"),
  )
)
FLOWER_MANIFESTS = {flower.name: flower.manifest for flower in FLOWER_CATALOG}
FLOWER_DESCRIPTIONS = {flower.name: flower.description for flower in FLOWER_CATALOG}

# A day is far longer than any window stays still; past that, a bee interval is
# a typo rather than a preference. The tick and frame-rate limits are the same
# idea: a tenth of a hertz is not a poll anyone means to ask for, and no display
# refreshes faster than the upper bound.
BEE_LIMIT = 86400.0
TICK_LIMIT = 10.0
FPS_LIMIT = 240.0
DEFAULT_UNIQUE_FLOWERS = 12
DEFAULT_POLL_INTERVAL = 1 / 30
DEFAULT_FPS = 60.0
# The one setting with no flag. It is not a preference but a statement about
# how the process was started (Kitty ran us as its `shell`, so the shell we
# start needs the integration Kitty would have set up itself), and the launcher
# is the only thing in a position to know it.
KITTY_SHELL_VARIABLE = "FLOWER_CURSOR_KITTY_SHELL"


def _fall_back[T](message: str, default: T) -> T:
  """Say what was wrong with a setting and carry on without it.

  Every Kitty window runs this wrapper, so a typo in a config file has to
  cost a line on stderr rather than the shell the user was opening. A typo
  in a flag costs the same line, for the same reason: the two spellings are
  one setting, and a window is not worth losing to either of them.
  """
  print(f"flower-cursor: {message}", file=sys.stderr)
  return default


def _number(text: str, *, low: float, high: float) -> float:
  try:
    value = float(text)
  except ValueError:
    raise ValueError(f"{text!r} is not a number") from None
  if not math.isfinite(value) or not low <= value <= high:
    raise ValueError(f"{value:g} is outside {low:g} to {high:g}")
  return value


def _is_off(text: str) -> bool:
  """Whether a value is the one word that turns a setting off."""
  return text.strip().lower() == OFF


def _species(text: str) -> str | Path:
  """A bundled flower by name, `cycle` or `random`, or a manifest of your own by path."""
  if text in FLOWER_MANIFESTS or text in ORDERS:
    return text
  # A bundled name is a bare word, so anything that looks like a filename is
  # asking for artwork of its own rather than misspelling a flower.
  if "/" in text or text.endswith(".json"):
    manifest = Path(text).expanduser()
    if not manifest.is_file():
      raise ValueError(f"no flower manifest at {manifest}")
    return manifest
  raise ValueError(f"{text!r} is not a flower; there is {', '.join((*FLOWER_MANIFESTS, *ORDERS))}")


def _bee(text: str) -> float | None:
  """Seconds of stillness between visits, or None for no visitor at all."""
  return None if _is_off(text) else _number(text, low=0.0, high=BEE_LIMIT)


def _buzz(text: str) -> float:
  return 0.0 if _is_off(text) else _number(text, low=0.0, high=1.0)


def _log(text: str) -> Path | None:
  return None if _is_off(text) else Path(text).expanduser()


def _unique_flowers(text: str) -> int:
  try:
    value = int(text)
  except ValueError:
    raise ValueError(f"{text!r} is not a whole number") from None
  if value < 0:
    raise ValueError(f"{value} is fewer than none")
  return value


def _fps(text: str) -> float:
  return _number(text, low=1.0, high=FPS_LIMIT)


def _tick(text: str) -> float:
  value = _number(text, low=0.0, high=TICK_LIMIT)
  if value <= 0:
    raise ValueError("a tick of zero seconds never comes round")
  return value


@dataclass(frozen=True, slots=True)
class Setting:
  """One thing a window can be told, in both of the ways it can be told it."""

  name: str
  parse: Callable[[str], object]
  default: object
  default_text: str
  metavar: str
  summary: str
  development: bool = False

  @property
  def flag(self) -> str:
    return f"--{self.name}"

  @property
  def variable(self) -> str:
    return f"FLOWER_CURSOR_{self.name.upper().replace('-', '_')}"

  @property
  def attribute(self) -> str:
    return self.name.replace("-", "_")

  @property
  def help(self) -> str:
    return f"{self.summary} (default: {self.default_text}; {self.variable} sets it for every window)"


def flower_help() -> str:
  """Every bundled flower and what it looks like, for `--species --help`."""
  return "; ".join(f"{flower.name}: {flower.description}" for flower in FLOWER_CATALOG)


SETTINGS = (
  Setting(
    "species",
    _species,
    DEFAULT_SPECIES,
    DEFAULT_SPECIES,
    "NAME|cycle|random|PATH",
    f"which flower grows, by name or by a manifest of your own ({flower_help()}); "
    "cycle grows the next flower in that list at every new bloom, and random any other",
  ),
  Setting(
    "bee",
    _bee,
    DEFAULT_INTERVAL,
    f"{DEFAULT_INTERVAL:g}",
    "SECONDS|off",
    "how long the caret stays still before a bee visits, on average; the wait is "
    "randomized around it and starts over whenever the caret moves or the bee leaves, "
    "0 brings one as soon as the caret stops, and off grows the flower without a visitor",
  ),
  Setting(
    "buzz",
    _buzz,
    0.0,
    OFF,
    "VOLUME|off",
    "wingbeat audio while the bee is in flight, from off to 1; needs the sound extra",
  ),
  Setting(
    "fps",
    _fps,
    DEFAULT_FPS,
    f"{DEFAULT_FPS:g}",
    "N",
    "frames per second while the flower moves or sways, from 1 to "
    f"{FPS_LIMIT:g}; fewer cost less CPU and the animation keeps its speed",
  ),
  Setting(
    "unique-flowers",
    _unique_flowers,
    DEFAULT_UNIQUE_FLOWERS,
    f"{DEFAULT_UNIQUE_FLOWERS}",
    "N",
    "how many simultaneous cursors get a separately animated flower before the rest are drawn as copies of it",
    development=True,
  ),
  Setting(
    "log",
    _log,
    None,
    OFF,
    "PATH|off",
    "write every changed cursor cell to this file",
    development=True,
  ),
  Setting(
    "poll-interval",
    _tick,
    DEFAULT_POLL_INTERVAL,
    f"{DEFAULT_POLL_INTERVAL:.4g}",
    "SECONDS",
    "seconds between Kitty cursor-position queries",
    development=True,
  ),
)
SETTINGS_BY_NAME = {setting.name: setting for setting in SETTINGS}


@dataclass(frozen=True, slots=True)
class Settings:
  """Everything a window was told, already read and checked."""

  species: str | Path
  bee: float | None
  buzz: float
  unique_flowers: int
  log: Path | None
  poll_interval: float
  fps: float
  # Which spelling each value came from, for `--check` to report. A window
  # that behaves unexpectedly is usually being told something it was not
  # told here, and naming the source is what shortens that search.
  sources: Mapping[str, str]

  @property
  def order(self) -> str:
    """`cycle` or `random` when every bloom picks again, else `fixed`."""
    return self.species if self.species in ORDERS else FIXED

  @property
  def flowers(self) -> tuple[tuple[Path, str | None], ...]:
    """Every flower this window can grow, as a manifest and its catalog name.

    One for a named or a path-given species; the whole catalog, in its
    order, for `cycle` and `random`. The name is None when the artwork is
    the user's own.
    """
    if isinstance(self.species, Path):
      return ((self.species, None),)
    if self.species in ORDERS:
      return tuple((flower.manifest, flower.name) for flower in FLOWER_CATALOG)
    return ((FLOWER_MANIFESTS[self.species], self.species),)

  @property
  def manifest(self) -> Path:
    """The assets this window grows, whether named or given as a path.

    For `cycle` and `random`, the first flower of the catalog.
    """
    return self.flowers[0][0]

  @property
  def species_name(self) -> str | None:
    """The catalog name, or None when the artwork is the user's own.

    For `cycle` and `random`, the first flower of the catalog.
    """
    return self.flowers[0][1]


def resolve(
  arguments: Mapping[str, str | None] | None = None,
  environ: Mapping[str, str] | None = None,
) -> Settings:
  """Read every setting from the flags given, then the environment.

  A flag wins over a variable, a variable over the default, and an
  unreadable value of either costs one line on stderr and the default.
  """
  arguments = {} if arguments is None else arguments
  environ = os.environ if environ is None else environ
  # Heterogeneous by construction: each row parses to its own type, and the
  # dataclass below is where those types are named.
  values: dict[str, Any] = {}
  sources: dict[str, str] = {}
  for setting in SETTINGS:
    text = arguments.get(setting.name)
    source = setting.flag
    if text is None:
      text = environ.get(setting.variable, "").strip() or None
      source = setting.variable
    if text is None:
      values[setting.attribute] = setting.default
      sources[setting.name] = "default"
      continue
    try:
      values[setting.attribute] = setting.parse(text)
      sources[setting.name] = source
    except ValueError as error:
      values[setting.attribute] = _fall_back(f"{source} {error}, using {setting.default_text}", setting.default)
      sources[setting.name] = "default"
  return Settings(**values, sources=sources)


def kitty_shell_requested(environ: Mapping[str, str] | None = None) -> bool:
  """Whether Kitty started us as its `shell`, so integration has to be nested."""
  environ = os.environ if environ is None else environ
  return environ.get(KITTY_SHELL_VARIABLE, OFF).strip().lower() not in {OFF, ""}
