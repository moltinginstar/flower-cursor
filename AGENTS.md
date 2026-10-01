# Agent instructions

- Python 3.14 under uv. `make check` runs the tests and the linter, `make format` formats.
- Two-space indentation, 120 columns, and ruff’s default rules; `pyproject.toml` has the config.
- Kitty only, with no Kitty patch or fork: everything goes through the PTY wrapper and Kitty’s graphics protocol. [`docs/decisions/`](docs/decisions) explains why.
- Nothing may assume a particular shell. The wrapper runs whatever the user’s shell is.
- Every Kitty window runs `kitty/flower-cursor-shell`, so it must fail open: whatever breaks, the window still gets its shell.
- The artwork in `src/flower_cursor/assets/` is baked from the scenes in `blender/`. Change the scene and re-bake rather than editing the images.
- Try animation changes in a real Kitty window running your normal shell, not a recording or a scripted playback.
