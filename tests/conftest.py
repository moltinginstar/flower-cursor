"""The suite runs in the developer's own shell, and answers to nothing in it.

Every `FLOWER_CURSOR_*` variable is a real setting (species, buzz, bee), so a
developer who runs the flower with a species they like would otherwise see
tests fail on their machine and pass on everyone else's. Clearing the prefix
rather than a list keeps that true for settings added later. Tests that are
about the variables set their own with `monkeypatch.setenv`, which still works
on top of this, and children spawned through `subprocess` inherit the cleared
environment the same way the test process has it.

The one variable set here is the shared renderer's: off, so a test draws in
the process it is watching and never starts, or talks to, the renderer the
developer's own windows are using. Tests of the renderer point it at a
directory of their own.
"""

import os

import pytest

_PREFIX = "FLOWER_CURSOR_"


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
  for name in [name for name in os.environ if name.startswith(_PREFIX)]:
    monkeypatch.delenv(name)
  monkeypatch.setenv("FLOWER_CURSOR_DAEMON", "off")
