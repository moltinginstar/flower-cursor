"""The setup report says what is actually there, including installs done by hand."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from flower_cursor import check
from flower_cursor.settings import FlowerSpec

BLOCK = f"""{check.BEGIN_MARKER}
# Grown by flower-cursor.
shell /opt/flower-cursor/kitty/flower-cursor-shell
env FLOWER_CURSOR_SPECIES=crocus
{check.END_MARKER}
"""


def write(path: Path, text: str) -> Path:
    config = path / "kitty.conf"
    config.write_text(text, encoding="utf-8")
    return config


def test_the_managed_block_is_read_back_without_its_markers(tmp_path: Path) -> None:
    block = check._configured_block(check._read_config(write(tmp_path, f"font_size 14\n{BLOCK}")))
    assert block is not None
    assert check.BEGIN_MARKER not in block
    assert "env FLOWER_CURSOR_SPECIES=crocus" in block


def test_a_config_without_the_markers_has_no_block(tmp_path: Path) -> None:
    assert check._configured_block(check._read_config(write(tmp_path, "font_size 14\n"))) is None


def test_markers_in_the_wrong_order_are_not_a_block(tmp_path: Path) -> None:
    upside_down = f"{check.END_MARKER}\nshell /bin/sh\n{check.BEGIN_MARKER}\n"
    assert check._configured_block(check._read_config(write(tmp_path, upside_down))) is None


def test_a_missing_config_is_not_a_crash(tmp_path: Path) -> None:
    absent = check._read_config(tmp_path / "absent.conf")
    assert absent == ""
    assert check._configured_block(absent) is None
    assert check._unmanaged_shell(absent) is None


def test_a_hand_written_shell_line_is_found(tmp_path: Path) -> None:
    line = "shell /somewhere/else/kitty/flower-cursor-shell"
    config = check._read_config(write(tmp_path, f"{line}\n"))
    assert check._unmanaged_shell(config) == line


def test_another_shell_is_not_mistaken_for_ours(tmp_path: Path) -> None:
    assert check._unmanaged_shell(check._read_config(write(tmp_path, "shell /bin/fish\n"))) is None


def test_the_config_directory_follows_kitty_s_own_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KITTY_CONFIG_DIRECTORY", str(tmp_path))
    assert check.kitty_config_path() == tmp_path / "kitty.conf"


def test_the_config_directory_falls_back_to_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KITTY_CONFIG_DIRECTORY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert check.kitty_config_path() == tmp_path / "kitty" / "kitty.conf"


def test_an_unknown_species_is_reported_as_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWER_CURSOR_SPECIES", "tulip")
    statuses = {label: status for status, label, _ in check._environment_lines()}
    assert statuses["FLOWER_CURSOR_SPECIES"] == check._WARN


def test_a_known_species_is_reported_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWER_CURSOR_SPECIES", "crocus")
    lines = {label: (status, detail) for status, label, detail in check._environment_lines()}
    assert lines["FLOWER_CURSOR_SPECIES"] == (check._OK, "crocus")


def test_a_buzz_volume_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWER_CURSOR_BUZZ", "0.4")
    lines = {label: status for status, label, _ in check._environment_lines()}
    assert lines["FLOWER_CURSOR_BUZZ"] == check._OK


def test_an_unreadable_buzz_volume_is_reported_as_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWER_CURSOR_BUZZ", "loud")
    lines = {label: status for status, label, _ in check._environment_lines()}
    assert lines["FLOWER_CURSOR_BUZZ"] == check._WARN


def test_a_buzz_of_zero_is_reported_as_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWER_CURSOR_BUZZ", "0")
    lines = {label: status for status, label, _ in check._environment_lines()}
    assert lines["FLOWER_CURSOR_BUZZ"] == check._WARN


def test_a_disabled_window_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWER_CURSOR_DISABLE", "1")
    labels = [label for _, label, _ in check._environment_lines()]
    assert "FLOWER_CURSOR_DISABLE" in labels


def test_a_missing_flower_breaks_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    absent = FlowerSpec("ghost", "Nothing at all", Path("/nowhere/manifest.json"))
    monkeypatch.setattr(check, "FLOWER_CATALOGUE", (*check.FLOWER_CATALOGUE, absent))
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
    monkeypatch.setattr(check, "_kitty_version", lambda: "kitty 0.49.0")
    monkeypatch.delenv("FLOWER_CURSOR_DISABLE", raising=False)
    monkeypatch.delenv("FLOWER_CURSOR_SPECIES", raising=False)
    monkeypatch.delenv("FLOWER_CURSOR_BUZZ", raising=False)
    # Only the flowers whose assets are committed, so this says something about
    # the report rather than about who has rendered what lately.
    monkeypatch.setattr(
        check,
        "FLOWER_CATALOGUE",
        tuple(flower for flower in check.FLOWER_CATALOGUE if flower.manifest.exists()),
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


def test_the_report_refuses_to_promise_the_graphics_protocol_works(installed: None) -> None:
    stream = io.StringIO()
    assert check.run_check(stream) == 0
    assert "does not test" in stream.getvalue()


def test_a_broken_install_exits_nonzero(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "launcher_path", lambda: None)
    stream = io.StringIO()
    assert check.run_check(stream) == 1
    assert "will not run" in stream.getvalue()


def test_the_markers_are_the_ones_install_sh_writes() -> None:
    """`install.sh` writes the block; this reads it. One of them is the source.

    They are separate copies in separate languages, so nothing but a test can
    stop an edit to the shell script from leaving `--check` reporting that a
    perfectly good install is not configured.
    """
    script = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")
    assert f'BEGIN="{check.BEGIN_MARKER}"' in script
    assert f'END="{check.END_MARKER}"' in script
    assert f'shell $DIR/kitty/{check.LAUNCHER_NAME}"' in script
    assert "${XDG_DATA_HOME:-$HOME/.local/share}/flower-cursor" in script
