# Kitty integration

## How it draws

The observer runs a shell or editor in its own PTY. It polls Kitty's standard cursor-position reports, hides the native cursor while flower mode is active, and restores terminal state on exit:

```sh
uv run flower-cursor --log /tmp/flower-cursor.log
```

The renderer uses the layered artwork bundled with the package: independent shedding petals, tip-first stalk wilt, and phase-linear idle. It composites authored RGBA layers into native-pixel PNGs. The expanded transparent canvas doubles the nominal 5×2-cell footprint while preserving the flower's scale and root registration, and crops imagery at the left edge rather than shifting the root. It uses absolute CUP positioning inside save/restore pairs, `C=1` to prevent graphics placement from advancing the cursor, and foreground `z=1`.

Each flower owns two alternating image slots. Changed frames are uploaded, the replacement is drawn, and the previous image data is freed. Identical pixels are not retransmitted. Main and alternate screens have separate placement ownership. Clears and switches retire the current flowers, delete restored owned placements at a safe write boundary, and wait for fresh cursor coordinates. Delayed observer reports from the previous screen are discarded; application cursor replies retain request order.

## Writing into someone else's stream

The flower's graphics go into the same stream as everything the child writes, so they must never land in the middle of the child's own escape sequences. A VT framer (`vt_framer.py`, after the DEC VT500-series parser) follows the child's output and knows where every sequence ends; flower writes and cursor queries wait for a boundary. Child bytes are forwarded as they arrive, never buffered and never cut short, so a slow multipart image upload just holds the flower back until it finishes. The only timeout is a 5 s guard against the wrapper's own bookkeeping getting stuck.

Automated PTY tests cover multipart ordering, ordered child cursor replies, stale report generations, startup output, and cursor restoration. By hand, I've used it in zsh and nvim: entering and leaving, scrolling, Ctrl-L, and exiting the shell. Rapid movement, reversals, resizes, and the edges of the terminal haven't been put through anything systematic.

## Every window: the `shell` option

Kitty starts each new window, tab, and OS window by running the program named by
the `shell` option. Pointing that option at the launcher makes the flower the
normal cursor in every window Kitty opens from then on, with no nested shell
session and no per-window command:

```conf
shell /absolute/path/to/flower-cursor/kitty/flower-cursor-shell
```

Run `uv sync` in the checkout first. Reload with `ctrl+shift+f5` or restart
Kitty; either way **already-open windows keep the shell they started with** and
are never re-launched.

The launcher rebuilds the startup Kitty would otherwise have done itself, so
nothing about the shell session changes:

- Your real `$SHELL` runs, not a substitute. The launcher refuses to treat
  itself as the shell, so the `shell` option cannot recurse into it.
- Shell integration is preserved by nesting `kitten run-shell --shell <path>`,
  the supported way to start an integrated shell from a non-shell parent. Prompt
  marks (OSC 133), `clone-in-kitty`, cwd reporting (OSC 7), and `argv0=-zsh`
  login startup all still work, alongside your own prompt, theme, and hooks.
- `env read_from_shell=…` still works. Kitty collects those variables by running
  `<shell> -l -i -c 'command env -0'` and giving it 1.5 seconds; the launcher
  recognises being called with arguments and hands that invocation straight to
  the real shell before doing anything else, forking nothing on the way, so it
  adds no measurable time: the read took 278 ± 10 ms through the launcher and
  through zsh alone alike, most of it the shell's own startup.
- The wrapper follows the shell's OSC 7 reports and changes its own directory to
  match, so `new_window_with_cwd`, `new_tab_with_cwd`, and
  `launch --cwd=current` open in the directory you are actually in.

### Turning it off

- Per window: start one without the launcher. `kitty --override shell=$SHELL`
  opens a plain window, and `kitty @ launch --type=tab $SHELL` a plain tab in
  the window you are already in. There is no setting of ours for this: Kitty
  decides what a window's shell is, and a variable of our own would only be a
  second, worse way to say it.
- Globally: remove or comment out the `shell` line and reload the config. Open
  windows are unaffected either way; the change applies to windows opened after
  it.
- `FLOWER_CURSOR_KITTY_SHELL` is set by the launcher itself, not by you. It is
  how the wrapper knows Kitty started it as a `shell` and that the shell it
  starts therefore needs `kitten run-shell` nesting; run `flower-cursor` by hand
  and it is absent, because you are already inside an integrated shell.
- The launcher also fails open. A missing interpreter, a half-finished checkout,
  or a broken asset load hands the window its ordinary shell rather than leaving
  an empty one behind.

### What it costs

Drawing the flower costs about 10–12% of one core in the flower's own
processes, sustained even at an idle prompt, because the renderer animates at
60 Hz and polls the cursor at 30 Hz; a bee visit adds a point or two. Kitty's
own share of drawing it varied too much between identical runs to quote
closely: 7–29% of a core on top of what a bare zsh window costs it. Only the
focused window draws — the rest stop their timers and use no measurable CPU —
so that is the cost of the terminal you are looking at, not a toll on each one
you leave open. Kitty never says whether a new window starts focused, so one
that opens in the background, such as a tab launched with `--keep-focus`,
draws for its first 30 seconds and then stops until you visit it.

Memory is shared. One renderer process per user draws for every wrapped
window: it holds Pillow, the decoded artwork and the audio device once, and
exits a minute after its last window closes. Each window keeps a thin wrapper
of about 28 MB resident (17 MB physical footprint), whichever flower it grows,
in place of the 10 MB `login` process a plain Kitty window runs; the shell's
own 8 MB is the same either way. The renderer is about 38 MB with one species
loaded, plus a few megabytes for each further species (about 11 MB for the
dahlia). `cycle` and `random` hold each species once it has bloomed, so a
renderer that has shown all seven carries about 45 MB more than one showing a
single species (28 MB of it decoded artwork). Three windows cost about 120 MB
either way; each window after that saves about 15 MB, and more with the buzz
on. [`docs/shared-renderer.md`](../docs/shared-renderer.md) has the design.

The buzz opens an audio device only while a bee is audible and stops it once
the bee falls silent, so it costs no CPU in a window you are not looking at.
The device's memory stays once it has been opened: about 35 MB, in the
renderer, once for every window, rather than in each window that ever buzzed
(75 MB per window drawing in its own process).

If the renderer cannot be started, or goes away, a window draws in its own
process instead, with no visible difference: about 45 MB resident for the
anemone, crocus, orchid and poppy, 50–60 MB for the cornflower and daisy, and
65 MB for the dahlia. `FLOWER_CURSOR_DAEMON=off` forces that; it is a
diagnostic, not a setting.

Measured on an Apple M4 Max (macOS 27.0, Kitty 0.49.0) with the poppy, in
dedicated Kitty instances started from my own `kitty.conf`, three wrapped windows each, as
cumulative process CPU over 60 s with focus reports sent to each window;
memory after the caret had moved around a line.

One window is also permanently "not at a prompt" from Kitty's point of view.
Kitty decides that from cursor visibility (`screen_cursor_at_a_shell_prompt`
returns early when DECTCEM is off), and hiding the native cursor is the whole
premise of the flower. The visible consequence is that
`confirm_os_window_close` treats every wrapped window as running a program and
asks before closing it. The cwd-following above exists precisely because
`--cwd=current` falls back to the foreground process's directory for the same
reason.

## One window at a time

A normally opened Kitty tab has a separate PTY and is not automatically wrapped
unless the `shell` option above is set. For one explicit tab:

```sh
kitty @ launch --type=tab /absolute/path/to/flower-cursor/kitty/flower-cursor-shell
```

Remote control must be enabled for that command.

The Python wheel bundles the artwork and exposes `flower-cursor`; installing it
into a chosen environment is an alternative to this checkout-based launcher.
Global and per-window configuration both remain explicitly opt-in.
