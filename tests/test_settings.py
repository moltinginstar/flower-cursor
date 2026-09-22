"""One setting, two spellings: the flag, the variable, and what wins."""

import math
from pathlib import Path

import pytest

from flower_cursor.bee import DEFAULT_INTERVAL
from flower_cursor.observer import build_parser
from flower_cursor.settings import (
    BEE_LIMIT,
    DEFAULT_MANIFEST,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RENDER_INTERVAL,
    DEFAULT_SPECIES,
    DEFAULT_UNIQUE_FLOWERS,
    FLOWER_MANIFESTS,
    KITTY_SHELL_VARIABLE,
    SETTINGS,
    TICK_LIMIT,
    kitty_shell_requested,
    resolve,
)

DEFAULTS = {
    "species": DEFAULT_SPECIES,
    "bee": DEFAULT_INTERVAL,
    "buzz": 0.0,
    "unique_flowers": DEFAULT_UNIQUE_FLOWERS,
    "log": None,
    "poll_interval": DEFAULT_POLL_INTERVAL,
    "render_interval": DEFAULT_RENDER_INTERVAL,
}
# Two readable values per setting, and what resolving each should produce.
# Two, because telling a flag from a variable needs them to disagree.
SAMPLES = {
    "species": (("cornflower", "cornflower"), ("daisy", "daisy")),
    "bee": (("45", 45.0), ("180", 180.0)),
    "buzz": (("0.4", 0.4), ("1", 1.0)),
    "unique-flowers": (("3", 3), ("0", 0)),
    "log": (("/tmp/cells.log", Path("/tmp/cells.log")), ("/tmp/other.log", Path("/tmp/other.log"))),
    "poll-interval": (("0.5", 0.5), ("0.25", 0.25)),
    "render-interval": (("0.25", 0.25), ("0.5", 0.5)),
}


def value(settings, name):
    return getattr(settings, name.replace("-", "_"))


def test_every_setting_has_both_spellings_and_they_are_the_only_two():
    # A setting reachable only as a flag cannot be set from a Kitty config at
    # all, so the table is what guarantees there is no such setting.
    assert {setting.name for setting in SETTINGS} == set(SAMPLES)
    for setting in SETTINGS:
        assert setting.flag == f"--{setting.name}"
        assert setting.variable.startswith("FLOWER_CURSOR_")
        assert setting.variable in setting.help


def test_nothing_given_is_every_default():
    settings = resolve({}, {})
    for attribute, default in DEFAULTS.items():
        assert getattr(settings, attribute) == default
    assert set(settings.sources.values()) == {"default"}


@pytest.mark.parametrize("name", list(SAMPLES))
def test_a_variable_sets_it_and_a_flag_beats_the_variable(name):
    setting = next(s for s in SETTINGS if s.name == name)
    (text, expected), (other, _) = SAMPLES[name]

    from_variable = resolve({}, {setting.variable: f" {other} "})
    assert value(from_variable, name) == SAMPLES[name][1][1]
    assert from_variable.sources[name] == setting.variable

    # A window has to be able to disagree with the environment it was started
    # in, or a variable in kitty.conf would pin every window it opened.
    from_flag = resolve({name: text}, {setting.variable: other})
    assert value(from_flag, name) == expected
    assert from_flag.sources[name] == setting.flag


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("species", "coral"),
        ("species", "off"),
        ("bee", "soon"),
        ("bee", str(BEE_LIMIT + 1)),
        ("bee", "-1"),
        ("bee", "nan"),
        ("buzz", "loud"),
        ("buzz", "1.5"),
        ("unique-flowers", "half"),
        ("unique-flowers", "-1"),
        ("poll-interval", "off"),
        ("poll-interval", "0"),
        ("render-interval", str(TICK_LIMIT + 1)),
    ],
)
def test_an_unreadable_value_costs_a_line_and_keeps_the_default(capsys, name, text):
    setting = next(s for s in SETTINGS if s.name == name)
    settings = resolve({name: text}, {})
    assert value(settings, name) == DEFAULTS[setting.attribute]
    assert settings.sources[name] == "default"
    # Named by the spelling it came from, so the fix is in the right file.
    assert setting.flag in capsys.readouterr().err


def test_an_unreadable_variable_names_the_variable(capsys):
    assert resolve({}, {"FLOWER_CURSOR_SPECIES": "cornfower"}).species == DEFAULT_SPECIES
    error = capsys.readouterr().err
    assert "FLOWER_CURSOR_SPECIES" in error and "cornfower" in error


@pytest.mark.parametrize("word", ["off", " OFF ", "Off"])
def test_one_word_turns_off_everything_that_can_be_turned_off(word):
    settings = resolve({"bee": word, "buzz": word, "log": word}, {})
    assert settings.bee is None
    assert settings.buzz == 0.0
    assert settings.log is None


def test_a_flag_brings_the_bee_back_to_a_window_the_environment_silenced():
    settings = resolve({"bee": "90"}, {"FLOWER_CURSOR_BEE": "off"})
    assert settings.bee == 90.0


def test_zero_seconds_is_a_setting_rather_than_a_way_to_say_off():
    settings = resolve({"bee": "0"}, {})
    assert settings.bee == 0.0


def test_every_bundled_flower_resolves_to_its_own_artwork():
    for name, manifest in FLOWER_MANIFESTS.items():
        settings = resolve({"species": name}, {})
        assert settings.species_name == name
        assert settings.manifest == manifest
    assert resolve({}, {}).manifest == DEFAULT_MANIFEST


def test_artwork_of_your_own_is_named_by_path(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    settings = resolve({"species": str(manifest)}, {})
    assert settings.manifest == manifest
    # Nothing in the catalogue to name, and nothing that second-guesses it.
    assert settings.species_name is None


def test_a_path_that_is_not_there_is_a_typo_rather_than_artwork(capsys, tmp_path):
    missing = tmp_path / "nowhere.json"
    assert resolve({"species": str(missing)}, {}).species_name == DEFAULT_SPECIES
    assert str(missing) in capsys.readouterr().err


@pytest.mark.parametrize("name", list(SAMPLES))
def test_the_parser_carries_every_setting_through_untouched(name):
    # argparse takes no part in what a value means: it hands the text over and
    # `resolve` is the only thing that reads it, flag and variable alike.
    text = SAMPLES[name][0][0]
    args = build_parser().parse_args([f"--{name}", text])
    assert getattr(args, name.replace("-", "_")) == text
    assert getattr(build_parser().parse_args([]), name.replace("-", "_")) is None


def test_the_kitty_shell_variable_is_the_one_setting_with_no_flag():
    assert "--kitty-shell" not in build_parser().format_help()
    assert KITTY_SHELL_VARIABLE not in build_parser().format_help()
    assert kitty_shell_requested({}) is False
    assert kitty_shell_requested({KITTY_SHELL_VARIABLE: "off"}) is False
    assert kitty_shell_requested({KITTY_SHELL_VARIABLE: "on"}) is True


def test_no_flag_is_spelled_as_a_negation():
    assert "--no-" not in build_parser().format_help()


@pytest.mark.parametrize("value_", [math.inf, math.nan])
def test_an_infinite_interval_is_not_a_schedule(capsys, value_):
    assert resolve({"bee": str(value_)}, {}).bee == DEFAULT_INTERVAL
    assert capsys.readouterr().err
