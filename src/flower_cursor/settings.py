"""What a user can ask of the cursor, and where the flowers it names live.

Both the wrapper and `--check` need this: one to grow the flower, the other to
say what would grow. Keeping it here is what lets the report describe an
install without importing the wrapper that performs one.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .bee import DEFAULT_INTERVAL

DEFAULT_SPECIES = "anemone"
DEFAULT_MANIFEST = Path(__file__).with_name("assets") / "layered" / "manifest.json"


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
FLOWER_CATALOGUE = tuple(
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
FLOWER_MANIFESTS = {flower.name: flower.manifest for flower in FLOWER_CATALOGUE}
FLOWER_DESCRIPTIONS = {flower.name: flower.description for flower in FLOWER_CATALOGUE}
# Kitty's `shell` option runs a program with no arguments of its own, so the
# species a user wants in every window has to arrive some other way.
SPECIES_VARIABLE = "FLOWER_CURSOR_SPECIES"
# Same reason: the buzz has to be switchable from a Kitty config that cannot
# pass arguments. It carries a volume rather than a flag, because a separate
# on/off switch alongside a volume is two ways to say silent that can disagree.
BUZZ_VARIABLE = "FLOWER_CURSOR_BUZZ"
# What a bare `--buzz` means. Zero is off, and off is the default.
BUZZ_DEFAULT = 1.0
# Same reason again, for the bee itself: a window opened from a Kitty config
# needs a way to say how often it is visited, and a way to say never. The
# interval is the average stillness a visit waits for; the switch is separate
# from it because zero seconds is a real setting - a bee the moment the caret
# stops - and not a way of asking for no bee at all.
BEE_INTERVAL_VARIABLE = "FLOWER_CURSOR_BEE_EVERY"
BEE_VARIABLE = "FLOWER_CURSOR_BEE"
BEE_INTERVAL_DEFAULT = DEFAULT_INTERVAL
# A day is far longer than any window stays still; past that, the number is a
# typo rather than a preference.
BEE_INTERVAL_LIMIT = 86400.0
# What `FLOWER_CURSOR_BEE` has to say to keep the bee away. Anything else is a
# yes, so a variable left over from some other tool cannot silently empty the
# screen of visitors.
BEE_OFF_WORDS = frozenset({"0", "off", "no", "false", "none", "never"})


def _fall_back[T](message: str, default: T) -> T:
    """Say what was wrong with a setting and carry on without it.

    Every Kitty window runs this wrapper, so a typo in a config file has to
    cost a line on stderr rather than the shell the user was opening.
    """
    print(f"flower-cursor: {message}", file=sys.stderr)
    return default


def selected_species(argument: str | None, *, overridden: bool) -> str | None:
    """Which flower to grow, from the command line or the environment.

    An unreadable name is worth a word but not a dead window: every Kitty
    window runs this, so a typo in a config file falls back to the default
    flower rather than leaving the user with no shell at all.
    """
    if argument is not None or overridden:
        return argument
    species = os.environ.get(SPECIES_VARIABLE) or None
    if species is not None and species not in FLOWER_MANIFESTS:
        return _fall_back(f"unknown flower {species!r}, using the default", None)
    return species


def buzz_volume(argument: float | None) -> float:
    """How loud the bee is, from the command line or the environment.

    Silent unless asked for, and zero is the same silence as never asking, so
    there is one number rather than a flag and a volume that can disagree. An
    unreadable value is worth a word but not a dead window, the same way an
    unknown flower name is.
    """
    value = argument
    if value is None:
        text = os.environ.get(BUZZ_VARIABLE, "").strip()
        if not text:
            return 0.0
        try:
            value = float(text)
        except ValueError:
            return _fall_back(f"unreadable {BUZZ_VARIABLE} {text!r}, staying silent", 0.0)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return _fall_back(f"buzz volume {value!r} is outside 0 to 1, staying silent", 0.0)
    return value


def bee_interval(argument: float | None) -> float:
    """How long the screen stays still before a bee visits, on average.

    One number covers every wait: the first one after a window opens, the one
    after the caret moves, and the one after a visit ends. An unreadable value
    is worth a word but not a dead window, the same way an unknown flower name
    is.
    """
    value = argument
    if value is None:
        text = os.environ.get(BEE_INTERVAL_VARIABLE, "").strip()
        if not text:
            return BEE_INTERVAL_DEFAULT
        try:
            value = float(text)
        except ValueError:
            return _fall_back(
                f"unreadable {BEE_INTERVAL_VARIABLE} {text!r}, using "
                f"{BEE_INTERVAL_DEFAULT:g} seconds",
                BEE_INTERVAL_DEFAULT,
            )
    if not math.isfinite(value) or not 0.0 <= value <= BEE_INTERVAL_LIMIT:
        return _fall_back(
            f"bee interval {value!r} is outside 0 to {BEE_INTERVAL_LIMIT:g} seconds, using "
            f"{BEE_INTERVAL_DEFAULT:g}",
            BEE_INTERVAL_DEFAULT,
        )
    return value


def bee_enabled(disabled: bool) -> bool:
    """Whether this window gets a visitor at all.

    `--no-bee` is the local answer and wins; otherwise the environment decides,
    and saying nothing means yes. Only the words that plainly mean no turn the
    bee off, so a variable set for some other purpose leaves the flower its
    visitor.
    """
    if disabled:
        return False
    return os.environ.get(BEE_VARIABLE, "").strip().lower() not in BEE_OFF_WORDS


def flower_help() -> str:
    """Every bundled flower and what it looks like, for `--flower --help`."""
    return "; ".join(f"{flower.name}: {flower.description}" for flower in FLOWER_CATALOGUE)
