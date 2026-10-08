# Flower Cursor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/moltinginstar/flower-cursor/actions/workflows/ci.yml/badge.svg)](https://github.com/moltinginstar/flower-cursor/actions/workflows/ci.yml)

Flower Cursor turns Kitty's cursor into a little flower. It blooms wherever your cursor stops and wilts when it moves on. Sit still long enough and it might get a visitor.

## Requirements

- [Kitty](https://sw.kovidgoyal.net/kitty/) on macOS or Linux (tested with 0.49)
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/moltinginstar/flower-cursor/main/install.sh | sh
```

This clones Flower Cursor into `~/.local/share/flower-cursor` and prints the settings to add to your `kitty.conf`. To clone it somewhere else, put `FLOWER_CURSOR_DIR=/some/path` before `sh`, and use that path below. To have the settings written for you instead:

```bash
~/.local/share/flower-cursor/install.sh --configure
~/.local/share/flower-cursor/install.sh --species poppy --configure
```

`--uninstall` takes them back out. Running the `curl` command again updates Flower Cursor.

Then restart Kitty or reload its config.

If nothing happens:

```bash
uv run --directory ~/.local/share/flower-cursor flower-cursor --check
```

## Configuration

Every setting is both a `--flag` for one window and a `FLOWER_CURSOR_` variable for all of them. In `kitty.conf` you set them with `env` lines:

```bash
# Which flower grows: a name, a manifest of your own, or cycle/random (default: anemone)
env FLOWER_CURSOR_SPECIES=cornflower

# How loud the bee is, from 0 to 1, or off (default: off)
env FLOWER_CURSOR_BUZZ=0.4

# Average seconds of stillness between bee visits, or off (default: 90)
env FLOWER_CURSOR_BEE=180
```

`flower-cursor --help` lists every setting.

### Flowers

| Value        | Description                                                    |
| ------------ | -------------------------------------------------------------- |
| `anemone`    | Pink Japanese anemone (default)                                |
| `cornflower` | Blue cornflower                                                |
| `daisy`      | White daisy                                                    |
| `crocus`     | Violet crocus                                                  |
| `poppy`      | Icelandic poppy                                                |
| `dahlia`     | Ruby-red dahlia                                                |
| `orchid`     | Green-and-white slipper orchid                                 |
| `cycle`      | The next flower in this list at every bloom, starting anywhere |
| `random`     | Any flower but the last one at every bloom                     |

`--species` also takes a path, e.g., `--species ~/my-flower/manifest.json`, to grow artwork of your own.

### The bee

After about ninety seconds of stillness, a bee arrives, rests on the bloom for a few seconds, and leaves. Moving the cursor sends it away.

The bee is silent unless you ask for sound, which needs the `sound` extra:

```bash
uv sync --directory ~/.local/share/flower-cursor --no-dev --inexact --extra sound
env FLOWER_CURSOR_BUZZ=0.4   # in kitty.conf
```

## Running it by hand

You don't need to touch your Kitty config to try it:

```bash
cd ~/.local/share/flower-cursor
uv run flower-cursor
uv run flower-cursor --species orchid
uv run flower-cursor -- nvim
```

## Caveats

- Flower Cursor uses about 10–12% of a CPU core while a flower is on screen, on top of Kitty's own drawing. You can try lowering the frame rate (e.g., `--fps 30`) if that becomes an issue.
- To draw the flower, Flower Cursor runs your shell inside a PTY and writes images into its output. I haven't seen this break anything, but if a program's output ever looks garbled, this is the likely cause.
- Apps that draw their own cursor don't get a flower. However, Flower Cursor respects [DECTCEM](https://vt100.net/docs/vt510-rm/DECTCEM.html), so you never see two cursors. Most apps, including Neovim and Claude Code, use the terminal's cursor and get the flower.

Kitty only added [custom shaders](https://sw.kovidgoyal.net/kitty/custom-shaders/) once most of Flower Cursor was already built. They would likely solve both the CPU cost and the PTY risk, so I'll probably move to them at some point.

## Development

```bash
uv sync
make check
```

The artwork is baked from the Blender scenes in [`blender/`](blender), so you only need Blender to make new flowers. [`docs/`](docs) has the design notes.

## License

This project is licensed under the [MIT License](LICENSE).
