"""The setup report says what is actually there, including installs done by hand."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from flower_cursor import check
from flower_cursor.settings import KITTY_SHELL_VARIABLE, FlowerSpec, resolve


def rows(**given: str) -> dict[str, tuple[str, str]]:
  """The settings part of the report, by the setting it is about."""
  lines = check._setting_lines(resolve(given, {}))
  return {label: (status, detail) for status, label, detail in lines}


def test_a_known_species_is_reported_with_the_spelling_that_asked_for_it() -> None:
  status, detail = rows(species="crocus")["species"]
  assert status == check._OK
  assert detail == "crocus (from --species)"


def test_a_variable_is_reported_as_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("FLOWER_CURSOR_SPECIES", "crocus")
  lines = check._setting_lines(resolve())
  assert ("species", "crocus (from FLOWER_CURSOR_SPECIES)") in [(label, detail) for _, label, detail in lines]


def test_an_unknown_species_is_reported_as_the_default_it_fell_back_to(capsys) -> None:
  # The report says what will grow, not what was asked for: the unreadable
  # value is already gone by the time anything is rendered.
  status, detail = rows(species="tulip")["species"]
  assert status == check._OK
  assert detail == "anemone (from the default)"
  assert "tulip" in capsys.readouterr().err


def test_the_bee_is_reported_in_the_words_the_setting_is_about() -> None:
  assert rows(bee="90")["bee"][1] == "a bee about every 90s (from --bee)"
  assert rows(bee="off")["bee"][1] == "no bee visits (from --bee)"


def test_a_buzz_volume_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(check, "have_miniaudio", lambda: True)
  assert rows(buzz="0.4")["buzz"] == (check._OK, "0.4 (from --buzz)")


def test_a_buzz_without_the_sound_extra_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(check, "have_miniaudio", lambda: False)
  status, detail = rows(buzz="0.4")["buzz"]
  assert status == check._WARN
  assert "sound extra" in detail


def test_fps_is_reported_without_a_trailing_zero() -> None:
  assert rows()["fps"][1] == "60 (from the default)"


def test_an_unreadable_buzz_volume_is_reported_as_the_silence_it_became(capsys) -> None:
  assert rows(buzz="loud")["buzz"][1] == "silent (from the default)"
  assert "loud" in capsys.readouterr().err


def test_a_buzz_with_no_bee_to_make_it_is_a_warning() -> None:
  status, detail = rows(buzz="0.4", bee="off")["buzz"]
  assert status == check._WARN
  assert "no bee" in detail


def test_a_development_knob_is_only_reported_once_someone_touches_it() -> None:
  assert "poll-interval" not in rows()
  assert rows(**{"poll-interval": "0.5"})["poll-interval"][1] == "0.5 (from --poll-interval)"


def test_the_nested_shell_integration_is_reported_when_kitty_asked_for_it(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv(KITTY_SHELL_VARIABLE, "on")
  assert KITTY_SHELL_VARIABLE in rows()


def test_a_missing_flower_breaks_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
  absent = FlowerSpec("ghost", "Nothing at all", Path("/nowhere/manifest.json"))
  monkeypatch.setattr(check, "FLOWER_CATALOG", (*check.FLOWER_CATALOG, absent))
  lines, broken = check.report()
  assert broken
  assert any(status == check._BAD and label == "flowers" for status, label, _ in lines)


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
  """A machine with Kitty on it, so these tests do not need one.

  The checkout supplies the launcher and the flower assets for real; only
  Kitty itself is stood in for, because CI has no reason to install a
  terminal emulator to find out whether we can read a config file.
  """
  monkeypatch.setattr(check, "_kitty_version", lambda: "0.49.0")
  for setting in check.SETTINGS:
    monkeypatch.delenv(setting.variable, raising=False)
  monkeypatch.delenv(KITTY_SHELL_VARIABLE, raising=False)
  # Only the flowers whose assets are committed, so this says something about
  # the report rather than about who has rendered what lately.
  monkeypatch.setattr(
    check,
    "FLOWER_CATALOG",
    tuple(flower for flower in check.FLOWER_CATALOG if flower.manifest.exists()),
  )


def test_an_install_that_is_all_there_reports_no_breakage(installed: None) -> None:
  _, broken = check.report()
  assert not broken


def test_a_terminal_that_is_not_kitty_is_a_warning_not_a_failure(
  installed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
  lines, broken = check.report()
  assert not broken
  assert any(status == check._WARN and label == "terminal" for status, label, _ in lines)


def test_kitty_missing_altogether_breaks_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(check, "_kitty_version", lambda: None)
  lines, broken = check.report()
  assert broken
  assert any(status == check._BAD and label == "kitty" for status, label, _ in lines)


def test_a_working_install_exits_zero(installed: None) -> None:
  stream = io.StringIO()
  assert check.run_check(stream=stream) == 0
  assert "Everything the flower cursor needs is here." in stream.getvalue()


def test_a_broken_install_exits_nonzero(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(check, "launcher_path", lambda: None)
  stream = io.StringIO()
  assert check.run_check(stream=stream) == 1
  assert "will not run" in stream.getvalue()


def test_the_launcher_is_looked_for_where_install_sh_puts_it() -> None:
  script = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")
  assert f'shell $DIR/kitty/{check.LAUNCHER_NAME}"' in script
  assert "${XDG_DATA_HOME:-$HOME/.local/share}/flower-cursor" in script
