"""One parsed copy of a flower manifest, for everything that reads one."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def manifest_data(manifest_path: Path) -> dict:
  """The parsed manifest, shared by everything that reads one.

  A manifest is a build artifact that does not change while a window is
  open, and a runtime, a compositor and a pose library each want it; without
  this every one of them reparses it, once per cursor on screen. Callers
  read it and do not mutate it, because they are all holding the same dict.
  """
  return json.loads(manifest_path.read_text())
