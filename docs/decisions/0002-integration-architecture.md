# 0002: Stay a PTY wrapper; there is no supported way to draw from outside

- **Status:** accepted
- **Date:** 2026-09-21
- **Constraint:** no Kitty patch or fork

## Question

Can cursor observation and drawing move onto a supported Kitty-side surface, so Kitty stays
responsible for terminal parsing instead of the wrapper duplicating it?

## Surfaces surveyed (Kitty 0.48.2)

| Surface | Can observe the cursor? | Can draw at a cell? | Verdict |
| --- | --- | --- | --- |
| Remote control (`kitten @`, 40+ commands) | **Yes**: `get-text --add-cursor` appends the cursor position/style as ANSI codes, from Kitty’s own parser | No. No command injects escape sequences or graphics into a window’s screen | Observation only |
| `kitten @ set-window-logo` | n/a | No. `window_logo_position` accepts 9 anchors (`top-left`…`bottom-right`), not cell coordinates | Rejected |
| Custom kittens | Via `boss`/remote control | No. A kitten runs as an overlay window for one invocation and is documented as unable to persist in the background or draw over an existing window. Kitty’s internals are undocumented and unstable | Rejected |
| `launch --type=overlay` / `overlay-main` | No | No. An overlay is a full opaque terminal window that takes input; it cannot pass input through to the window beneath | Rejected |
| Watchers (`watcher` in kitty.conf, applied to every window) | Lifecycle only: `on_load`, `on_resize`, `on_focus_change`, `on_close`, `on_title_change`, `on_set_user_var`, `on_cmd_startstop`, `on_color_scheme_preference_change`. **No cursor-movement event** | No | Rejected |
| `marks` / `create-marker` | n/a | Per-line regex highlight only, applied when a line changes | Rejected |
| Panel kitten | No | Separate window; on macOS since 0.42 but cannot cover other windows | Rejected |
| Native `cursor_trail` | Internal | Internal | Precedent that Kitty draws cursor decoration itself, with no extension point |

No supported surface can draw at a cell position in an existing window. Every pixel still has to
reach Kitty through the child’s output stream, so the PTY wrapper stays.

## What the wrapper has to get right

Two problems come with standing in the child’s stream.

1. **Framing.** Graphics injected in the middle of an OSC (clipboard, hyperlinks, cwd), a DCS
   (sixel, tmux passthrough, terminfo replies), a CSI split across reads, a two-byte `ESC`
   sequence, or a multi-byte UTF-8 character corrupt it. A timeout that gives up on an unfinished
   sequence cuts slow transfers short.
2. **Cursor reports.** Polling with `ESC[6n` and stripping every `ESC[<row>;<col>R` from the
   child’s input swallows the replies of applications that ask for their own position, Neovim
   included.

## Decision

| Concern | Choice |
| --- | --- |
| Cursor position source | DECXCPR (`ESC[?6n`). Kitty’s `report_device_status` answers a private query with `CSI ? y ; x R`, which cannot be mistaken for the plain CPR applications wait for |
| Application cursor queries | Pass through untouched |
| Output framing | A VT500-series state machine that frames sequences without emulating them; renderer writes only at a boundary |
| Slow or large transfers | Never truncated; the framer knows when a sequence ends |
| Drawing transport | Kitty graphics protocol via the child’s stdout |
| Activation | Kitty’s `shell` option runs the wrapper, so every new window gets a flower; already-open windows are left alone |
| Remote control | Not used. It would still be polling (there is no cursor event), it would race the bytes already in the stream the wrapper writes into, and it needs `allow_remote_control` and `listen_on` set globally |

### Keeping Kitty’s shell integration

Kitty gates both login-shell handling and shell integration on `argv[0] == shell_path`
(`kitty/child.py:364` in 0.48.2). Naming the wrapper as `shell` therefore skips the `/usr/bin/login -f -l -p`
wrapper (`child.py:490`) and makes `modify_shell_environ` bail at
`get_supported_shell_name(argv[0])` (`shell_integration.py:241`) before it even sets
`KITTY_SHELL_INTEGRATION`, silently losing prompt marks, jump-to-prompt, cmd start/stop, cwd
reporting and `last_cmd_output`.

So the wrapper runs Kitty’s own kitten rather than reimplementing integration:

    kitten run-shell --shell "$SHELL"

Integration coverage is then whatever Kitty supports: zsh, fish and bash. A shell Kitty has no
integration for (nushell, elvish) gets none, exactly as under plain Kitty, and the wrapper itself
is shell-agnostic, so those shells still get flowers.

### What it can’t reach

The `shell` setting reaches only shells Kitty launches in new windows. Already-open windows,
windows running a command directly (`kitty nvim`, `kitten @ launch htop`), tmux panes and shells
across ssh get no flower. With no fork and no drawing surface, no architecture reaches them.

## Multiple cursors

Kitty has supported the [multiple cursors protocol](https://sw.kovidgoyal.net/kitty/multiple-cursors-protocol/)
since 0.43. Applications announce their logical cursors with
`CSI > SHAPE ; COORD_TYPE : COORDS ; ... SP q`:

- shape `0` clears, `1`/`2`/`3` are block/beam/underline, `29` follows the main cursor’s shape,
  `30`/`40` set text and cursor colors, `100` queries currently set cursors
- coordinate type `2` takes `y:x` pairs, 1-based from the top-left; type `4` takes
  `top:left:bottom:right` rectangles; type `0` means the main cursor
- `printf "\e[>29;2:4:5 q"` places a cursor at row 4, column 5; `CSI > 0;4 SP q` clears all
- support is detected by sending `CSI > SP q`; a supporting terminal answers with its shape list,
  and no numbers means unsupported
- the terminal must remove all extra cursors on ED with parameter 2, 3 or 22, on reset, and on any
  switch between the main and alternate screens; IND and RI must leave them exactly in place

These arrive in the child’s output stream, which the framer already parses, so no editor-specific
integration is needed:

- placement sequences are forwarded to Kitty like any other output, and the wrapper keeps its own
  copy of the cursor set. On the next tick it clears Kitty’s copy with `CSI > 0;4 SP q`, so Kitty’s
  extra cursors don’t show under the flowers
- the support query (`CSI > SP q`) and its reply pass through untouched, so applications keep
  detecting support and keep emitting the protocol
- the state query (`CSI > 100 SP q`) still reaches Kitty, but Kitty’s reply, now empty, is swapped
  for the cursors the wrapper is tracking, so applications read back what they set
- up to 12 cursors (`--unique-flowers`) each get a flower with its own lifecycle; past that, every
  cursor shows a copy of the main flower, transmitted once (`a=T`) and placed at each cursor
  (`a=p`, about 60 bytes each)
- the protocol’s clearing rules (shape 0, ED 2/3/22, reset, screen switch) are the removal events,
  and IND/RI are explicitly not

Nothing here is specific to an editor. Neovim is one that emits the protocol.
