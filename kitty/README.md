# Kitty integration

## How it draws

The wrapper runs your shell in its own PTY, asks Kitty where the cursor is 30 times a second, hides the native cursor, and puts the terminal back when it exits. To log what it does:

```sh
uv run flower-cursor --log /tmp/flower-cursor.log
```

Each flower alternates between two image IDs, so a new frame never overwrites the one on screen, and an unchanged frame isn't sent again.

## Writing into someone else's stream

The flower's graphics go into the same stream as everything the child writes, so they must never land in the middle of the child's own escape sequences. A VT framer (`vt_framer.py`, after the DEC VT500-series parser) follows the child's output and knows where every sequence ends; flower writes and cursor queries wait for a boundary. Child bytes are forwarded as they arrive, never buffered and never cut short, so a slow multipart image upload just holds the flower back until it finishes. The only timeout is a 5 s guard against the wrapper's own bookkeeping getting stuck.

## Every window: the `shell` option

Kitty starts each new window, tab, and OS window by running the program named by the `shell` option. Pointing that option at the launcher makes the flower the normal cursor in every window Kitty opens from then on:

```conf
shell /absolute/path/to/flower-cursor/kitty/flower-cursor-shell
```

Run `uv sync` in the checkout first, then reload the config or restart Kitty. Windows that are already open keep the shell they started with.

The launcher rebuilds the startup Kitty would otherwise have done itself, so nothing about the shell session changes:

- Your real `$SHELL` runs, not a substitute. The launcher refuses to treat itself as the shell, so the `shell` option can't recurse into it.
- Shell integration is preserved by nesting `kitten run-shell --shell <path>`, the supported way to start an integrated shell from a non-shell parent. Prompt marks (OSC 133), `clone-in-kitty`, cwd reporting (OSC 7), and `argv0=-zsh` login startup all still work, alongside your own prompt, theme, and hooks.
- `env read_from_shell=…` still works. Kitty collects those variables by running `<shell> -l -i -c 'command env -0'` and giving it 1.5 seconds; the launcher hands any call with arguments straight to the real shell without forking, so it adds no measurable time.
- The wrapper follows the shell's OSC 7 reports and changes its own directory to match, so `new_window_with_cwd`, `new_tab_with_cwd`, and `launch --cwd=current` open in the directory you're actually in.
- The launcher fails open. A missing interpreter, a half-finished checkout, or a broken asset load hands the window its ordinary shell rather than leaving an empty one behind.

### Turning it off

- Per window: start one without the launcher. `kitty --override shell=$SHELL` opens a plain window, and `kitty @ launch --type=tab $SHELL` a plain tab in the window you're already in.
- Globally: remove or comment out the `shell` line and reload the config.

### What it costs

Drawing the flower costs about 10–12% of one core in the flower's own processes, sustained even at an idle prompt, because the renderer animates at 60 Hz and polls the cursor at 30 Hz; a bee visit adds a point or two. Kitty's own share of drawing it varied too much between identical runs to quote closely: 7–29% of a core on top of what a bare zsh window costs it. Only the focused window draws; the rest stop their timers and use no measurable CPU. So that is the cost of the terminal you're looking at, not a toll on each one you leave open. Kitty never says whether a new window starts focused, so one that opens in the background, such as a tab launched with `--keep-focus`, draws for its first 30 seconds and then stops until you visit it.

Memory is shared. One renderer process per user draws for every wrapped window: it holds Pillow, the decoded artwork and the audio device once, and exits a minute after its last window closes. Each window keeps a thin wrapper of about 28 MB resident (17 MB physical footprint), whichever flower it grows, in place of the 10 MB `login` process a plain Kitty window runs; the shell's own 8 MB is the same either way. The renderer is about 38 MB with one species loaded, plus a few megabytes for each further species (about 11 MB for the dahlia). `cycle` and `random` hold each species once it has bloomed, so a renderer that has shown all seven carries about 45 MB more than one showing a single species (28 MB of it decoded artwork). Three windows cost about 120 MB either way; each window after that saves about 15 MB, and more with the buzz on. [`docs/shared-renderer.md`](../docs/shared-renderer.md) has the design.

The buzz opens an audio device only while a bee is audible and stops it once the bee falls silent, so it costs no CPU in a window you're not looking at. The device's memory stays once it has been opened: about 35 MB, in the renderer, once for every window, rather than in each window that ever buzzed (75 MB per window drawing in its own process).

If the renderer can't be started, or goes away, a window draws in its own process instead, with no visible difference: about 45 MB resident for the anemone, crocus, orchid and poppy, 50–60 MB for the cornflower and daisy, and 65 MB for the dahlia. `FLOWER_CURSOR_DAEMON=off` forces that, for debugging.

Measured on an Apple M4 Max (macOS 27.0, Kitty 0.49.0) with the poppy, in dedicated Kitty instances started from my own `kitty.conf`, three wrapped windows each, as cumulative process CPU over 60 s with focus reports sent to each window; memory after the caret had moved around a line.

Every wrapped window is also permanently "not at a prompt" from Kitty's point of view. Kitty decides that from cursor visibility (`screen_cursor_at_a_shell_prompt` returns early when DECTCEM is off), and hiding the native cursor is the whole premise of the flower. The visible consequence is that `confirm_os_window_close` treats every wrapped window as running a program and asks before closing it. The cwd-following above exists because `--cwd=current` falls back to the foreground process's directory for the same reason.

## One window at a time

Without the `shell` option, you can wrap a single tab:

```sh
kitty @ launch --type=tab /absolute/path/to/flower-cursor/kitty/flower-cursor-shell
```

This needs `allow_remote_control`.
