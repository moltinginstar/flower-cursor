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


def flower_help() -> str:
    """Every bundled flower and what it looks like, for `--flower --help`."""
    return "; ".join(f"{flower.name}: {flower.description}" for flower in FLOWER_CATALOGUE)
