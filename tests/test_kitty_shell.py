"""The shell a Kitty `shell` line has to produce.

Kitty gives up on shell integration the moment its `shell` option names
something it does not recognize, so a wrapper installed there has to rebuild
the launch itself. These tests pin the shape of that launch.
"""

import os
from pathlib import Path

from flower_cursor.kitty_shell import kitten_executable, shell_command, user_shell


def _kitten(tmp_path: Path, relative: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_the_shell_comes_from_the_environment() -> None:
    assert user_shell({"SHELL": "/usr/bin/fish"}) == "/usr/bin/fish"


def test_our_own_launcher_is_never_taken_for_a_shell() -> None:
    """`SHELL` pointing at the wrapper would nest wrappers without end."""
    for name in ("flower-cursor", "flower-cursor-shell"):
        resolved = user_shell({"SHELL": f"/opt/bin/{name}"})
        assert Path(resolved).name != name


def test_a_missing_shell_variable_falls_back_to_the_password_database() -> None:
    import pwd

    expected = pwd.getpwuid(os.geteuid()).pw_shell or "/bin/sh"
    assert user_shell({}) == expected


def test_the_kitten_is_found_beside_the_kitty_that_launched_us(tmp_path: Path) -> None:
    """Kitty computes it as `dirname(kitty_exe())/kitten`; both layouts count."""
    macos = _kitten(tmp_path / "app", "Contents/MacOS/kitten")
    environ = {"KITTY_INSTALLATION_DIR": str(tmp_path / "app/Contents/Resources/kitty")}
    assert kitten_executable(environ) == str(macos)

    linux = _kitten(tmp_path / "usr", "bin/kitten")
    environ = {"KITTY_INSTALLATION_DIR": str(tmp_path / "usr/lib/kitty")}
    assert kitten_executable(environ) == str(linux)


def test_the_kitten_is_not_invented_when_kitty_is_not_around(tmp_path: Path) -> None:
    assert kitten_executable({"PATH": str(tmp_path)}) is None


def test_the_shell_is_named_outright_rather_than_guessed(tmp_path: Path) -> None:
    """`--shell .` walks up to kitty.conf's `shell` - which is this wrapper."""
    kitten = _kitten(tmp_path / "app", "Contents/MacOS/kitten")
    command = shell_command(
        {
            "SHELL": "/bin/zsh",
            "KITTY_INSTALLATION_DIR": str(tmp_path / "app/Contents/Resources/kitty"),
        }
    )
    assert command == [str(kitten), "run-shell", "--shell", "/bin/zsh"]


def test_without_a_kitten_the_users_shell_still_starts(tmp_path: Path) -> None:
    """Integration is lost outside Kitty; the shell itself must not be."""
    assert shell_command({"SHELL": "/bin/zsh", "PATH": str(tmp_path)}) == ["/bin/zsh", "-l", "-i"]
