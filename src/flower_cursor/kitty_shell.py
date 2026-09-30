"""Start the user's real shell the way Kitty itself would.

Kitty only sets up shell integration when the `shell` option names a shell it
recognizes: `modify_shell_environ` in `kitty/shell_integration.py` returns
early when `get_supported_shell_name(argv[0])` is None, and on macOS Kitty
reaches that setup by running the shell through `kitten run-shell` instead
(`Child.should_run_via_run_shell_kitten`, `kitty/child.py`). A wrapper
installed as `shell` is not a recognized shell, so a window started through it
would lose prompt marks, cwd reporting, `clone-in-kitty`, completions, and -
on macOS - login-mode rc files, because `run-shell` is also what execs the
shell with a `-` argv0 (`tools/tui/run.go`).

Nesting `kitten run-shell` inside our PTY restores all of it: the kitten sets
KITTY_SHELL_INTEGRATION and the shell-specific rc plumbing itself, from the
user's own `shell_integration` setting.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

# Names this project installs as a program. If one of them is what $SHELL
# points at, the variable is describing the wrapper rather than a shell, and
# honouring it would nest wrappers without end.
_OUR_NAMES = frozenset({"flower-cursor", "flower-cursor-shell"})


def user_shell(environ: Mapping[str, str] | None = None) -> str:
  """Resolve the user's login shell the way `kitty/constants.py` does."""
  environ = os.environ if environ is None else environ
  shell = environ.get("SHELL") or ""
  if shell and Path(shell).name not in _OUR_NAMES:
    return shell
  try:
    import pwd

    return pwd.getpwuid(os.geteuid()).pw_shell or "/bin/sh"
  except ImportError, KeyError:  # no pwd module, or a uid with no passwd entry
    return "/bin/sh"


def kitten_executable(environ: Mapping[str, str] | None = None) -> str | None:
  """Find the `kitten` binary that belongs to the running Kitty.

  Kitty computes it as `dirname(kitty_exe())/kitten`. The wrapper is spawned
  with the login environment, whose PATH need not contain it, so the install
  directory Kitty exports is the reliable route.
  """
  environ = os.environ if environ is None else environ
  installation = environ.get("KITTY_INSTALLATION_DIR")
  if installation:
    root = Path(installation)
    candidates = (
      # macOS: .../kitty.app/Contents/Resources/kitty -> .../MacOS/kitten
      root.parent.parent / "MacOS" / "kitten",
      # Linux: <prefix>/lib/kitty -> <prefix>/bin/kitten
      root.parent.parent / "bin" / "kitten",
      root.parent / "bin" / "kitten",
      root / "kitten",
    )
    for candidate in candidates:
      if os.access(candidate, os.X_OK) and candidate.is_file():
        return str(candidate)
  found = shutil.which("kitten", path=environ.get("PATH"))
  return found


def shell_command(environ: Mapping[str, str] | None = None) -> list[str]:
  """Build the command that starts the user's shell with integration.

  Without a kitten to nest - a non-Kitty terminal, or a Kitty whose install
  we cannot locate - the shell is started directly in login/interactive mode.
  It loses integration, but it is still the user's shell.
  """
  environ = os.environ if environ is None else environ
  shell = user_shell(environ)
  kitten = kitten_executable(environ)
  if kitten is None:
    return [shell, "-l", "-i"]
  # `--shell .` would ask the kitten to guess, and its guess walks up to the
  # `shell` option in kitty.conf - which is this wrapper. Naming the shell
  # outright is what keeps that from recursing.
  return [kitten, "run-shell", "--shell", shell]


def exec_fallback(command: list[str]) -> None:
  """Replace this process with *command*, as a last resort before dying.

  Installed as Kitty's `shell`, this wrapper stands between the user and
  every new window. Whatever goes wrong here, the window must still come up
  with a usable shell in it.
  """
  try:
    os.execvp(command[0], command)
  except OSError:
    shell = user_shell()
    try:
      os.execvp(shell, [shell, "-l", "-i"])
    except OSError:
      print(f"flower-cursor: cannot start a shell ({shell})", file=sys.stderr)
      os._exit(127)
