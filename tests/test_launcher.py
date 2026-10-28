"""The Kitty launcher starts the installed runtime and otherwise still leaves a usable terminal."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
  """A copy of the launcher and installer in a checkout of their own, with no runtime yet."""
  root = tmp_path / "checkout"
  (root / "kitty").mkdir(parents=True)
  shutil.copy2(REPOSITORY / "kitty" / "flower-cursor-shell", root / "kitty" / "flower-cursor-shell")
  shutil.copy2(REPOSITORY / "install.sh", root / "install.sh")
  (root / "pyproject.toml").write_text("")
  return root


def _script(path: Path, body: str) -> Path:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(f"#!/bin/sh\n{body}")
  path.chmod(0o755)
  return path


def _environment(tmp_path: Path) -> dict[str, str]:
  bin_directory = tmp_path / "bin"
  shell = _script(bin_directory / "user-shell", 'printf "shell:%s:%s\\n" "$PWD" "$*"\n')
  # A uv that fails loudly: opening a window must not need uv at all.
  _script(bin_directory / "uv", "echo uv-was-called >&2\nexit 97\n")
  # Exclude Kitty's kitten so the plain shell is observed directly.
  return {"PATH": f"{bin_directory}:/usr/bin:/bin", "SHELL": str(shell), "HOME": str(tmp_path)}


def _launch(checkout: Path, env: dict[str, str], cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    [str(checkout / "kitty" / "flower-cursor-shell"), *args],
    cwd=cwd,
    env=env,
    capture_output=True,
    text=True,
    timeout=20,
    check=False,
  )


def _fake_runtime(checkout: Path, body: str) -> None:
  _script(checkout / ".runtime" / "bin" / "python", body)


@pytest.mark.parametrize("virtual_env", [None, "/somewhere/else/.venv"])
def test_the_runtime_starts_with_the_windows_environment_untouched(
  checkout: Path, tmp_path: Path, virtual_env: str | None
) -> None:
  _fake_runtime(
    checkout,
    'printf "runtime:%s\\n" "$PWD" "$PATH" "${VIRTUAL_ENV-unset}" "$FLOWER_CURSOR_KITTY_SHELL" "$1"\n',
  )
  env = _environment(tmp_path)
  if virtual_env is not None:
    env["VIRTUAL_ENV"] = virtual_env
  result = _launch(checkout, env, tmp_path)
  assert result.returncode == 0
  assert "uv-was-called" not in result.stderr
  assert result.stdout.splitlines() == [
    f"runtime:{tmp_path}",
    f"runtime:{env['PATH']}",
    f"runtime:{virtual_env or 'unset'}",
    "runtime:on",
    "runtime:-I",
  ]


def test_the_shells_exit_status_is_the_windows(checkout: Path, tmp_path: Path) -> None:
  _fake_runtime(checkout, "exit 3\n")
  result = _launch(checkout, _environment(tmp_path), tmp_path)
  assert result.returncode == 3
  # No second shell after the first one exits: the window closes.
  assert "shell:" not in result.stdout


def test_a_missing_runtime_opens_the_users_shell(checkout: Path, tmp_path: Path) -> None:
  result = _launch(checkout, _environment(tmp_path), tmp_path)
  assert result.returncode == 0
  assert result.stdout == f"shell:{tmp_path}:-l -i\n"
  assert f"sh {checkout}/install.sh" in result.stderr


def test_a_runtime_whose_python_is_gone_opens_the_users_shell(checkout: Path, tmp_path: Path) -> None:
  # What a removed system Python leaves behind: the environment's interpreter
  # is a symlink to nothing.
  (checkout / ".runtime" / "bin").mkdir(parents=True)
  (checkout / ".runtime" / "bin" / "python").symlink_to(tmp_path / "removed" / "python3.14")
  result = _launch(checkout, _environment(tmp_path), tmp_path)
  assert result.returncode == 0
  assert result.stdout == f"shell:{tmp_path}:-l -i\n"
  assert "no working runtime" in result.stderr


def test_a_runtime_that_cannot_import_the_package_opens_the_users_shell(checkout: Path, tmp_path: Path) -> None:
  # A real interpreter with no flower_cursor installed in it.
  base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
  (checkout / ".runtime" / "bin").mkdir(parents=True)
  (checkout / ".runtime" / "bin" / "python").symlink_to(base)
  # Neither the user's PYTHONPATH nor the window's directory may supply it.
  env = _environment(tmp_path) | {"PYTHONPATH": str(REPOSITORY / "src")}
  result = _launch(checkout, env, REPOSITORY / "src")
  assert result.returncode == 0
  assert result.stdout == f"shell:{REPOSITORY / 'src'}:-l -i\n"
  assert "the runtime is broken" in result.stderr
  assert f"sh {checkout}/install.sh" in result.stderr


def test_the_fallback_marker_does_not_reach_the_shell(checkout: Path, tmp_path: Path) -> None:
  bin_directory = tmp_path / "bin"
  env = _environment(tmp_path)
  _script(bin_directory / "user-shell", 'printf "marker:%s\\n" "${FLOWER_CURSOR_LAUNCH_FAILED-unset}"\n')
  env["FLOWER_CURSOR_LAUNCH_FAILED"] = "1"
  result = _launch(checkout, env, tmp_path)
  assert result.stdout == "marker:unset\n"


def test_environment_lookup_goes_straight_to_the_shell(checkout: Path, tmp_path: Path) -> None:
  _fake_runtime(checkout, "echo runtime-was-started\nexit 99\n")
  result = _launch(checkout, _environment(tmp_path), tmp_path, "-l", "-i", "-c", "command env -0")
  assert result.returncode == 0
  assert result.stdout == f"shell:{tmp_path}:-l -i -c command env -0\n"


def _install(checkout: Path, tmp_path: Path, *args: str) -> list[str]:
  """Run the installer against a recording uv and return the sync it asked for."""
  env = _environment(tmp_path)
  _script(
    tmp_path / "bin" / "uv",
    f'printf "%s\\n" "UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT" "$@" > {tmp_path / "uv-call"}\n',
  )
  env["KITTY_CONFIG_DIRECTORY"] = str(tmp_path / "kitty-config")
  result = subprocess.run(
    ["sh", str(checkout / "install.sh"), *args], env=env, capture_output=True, text=True, timeout=20, check=False
  )
  assert result.returncode == 0, result.stderr
  return (tmp_path / "uv-call").read_text().splitlines()


def test_the_installer_provisions_a_private_managed_runtime(checkout: Path, tmp_path: Path) -> None:
  call = _install(checkout, tmp_path)
  assert call[0] == f"UV_PROJECT_ENVIRONMENT={checkout}/.runtime"
  assert call[1:3] == ["sync", "--directory"]
  assert {"--locked", "--no-dev", "--inexact", "--managed-python"} <= set(call)
  assert "--extra" not in call


def test_the_installer_adds_sound_when_asked(checkout: Path, tmp_path: Path) -> None:
  call = _install(checkout, tmp_path, "--sound")
  assert call[call.index("--extra") + 1] == "sound"


def test_an_update_keeps_sound_the_runtime_already_has(checkout: Path, tmp_path: Path) -> None:
  (checkout / ".runtime" / "lib" / "python3.14" / "site-packages" / "miniaudio-1.71.dist-info").mkdir(parents=True)
  call = _install(checkout, tmp_path)
  assert call[call.index("--extra") + 1] == "sound"


def test_the_installer_leaves_kitty_alone_when_the_runtime_fails(checkout: Path, tmp_path: Path) -> None:
  env = _environment(tmp_path) | {"KITTY_CONFIG_DIRECTORY": str(tmp_path / "kitty-config")}
  result = subprocess.run(
    ["sh", str(checkout / "install.sh"), "--configure"],
    env=env,
    capture_output=True,
    text=True,
    timeout=20,
    check=False,
  )
  assert result.returncode != 0
  assert not (tmp_path / "kitty-config" / "kitty.conf").exists()
