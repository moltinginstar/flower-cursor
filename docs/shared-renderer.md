# Shared renderer

A wrapped window needs a PTY of its own, but not its own copy of Pillow, the
decoded artwork, or an audio device. So each window runs a thin wrapper, and
one per-user process, the **renderer**, does the expensive part for all of
them. Without the renderer, a window draws in its own process, which is what
the “In-process” columns below describe.

## The split

| Responsibility                                      | In-process                      | Shared renderer                                      |
| --------------------------------------------------- | ------------------------------- | ---------------------------------------------------- |
| PTY, raw mode, child process, signals               | window                          | wrapper                                              |
| Output framing, graphics-upload arbiter             | window                          | wrapper                                              |
| Cursor-report polling and parsing                   | window                          | wrapper                                              |
| Focus reports, 30 s focus grace, typing             | window                          | wrapper                                              |
| DECTCEM caret ownership (`NativeCursorPolicy`)      | window                          | wrapper                                              |
| Multi-cursor tracking, suppression, state replies   | window                          | wrapper                                              |
| OSC 7 cwd following                                 | window                          | wrapper                                              |
| Viewport size (`TIOCGWINSZ`)                        | window                          | wrapper, sent with every tick                        |
| Screen-reset detection (ED 2/3/22, RIS, alt screen) | window, inside `FlowerRenderer` | wrapper detects, forwards the sequence               |
| Frame timing (60 Hz focused, idle when not)         | window                          | wrapper                                              |
| Flower lifecycle (`LayeredRuntime`), bee            | window                          | renderer, one per window                             |
| Extra-cursor flowers (`MultiCursorFlowers`)         | window                          | renderer, one per window                             |
| Compositing, PNG encoding, image ids                | window                          | renderer, one per window                             |
| Decoded layer images                                | window                          | renderer, one copy per species                       |
| Pillow and the artwork imports                      | window                          | renderer, once                                       |
| Buzz synthesis (`Voice`)                            | window                          | renderer, one per window                             |
| Audio device                                        | window, opened on first buzz    | renderer, **one** device mixing every window’s voice |
| Writing graphics to the terminal                    | window                          | wrapper, bytes received from the renderer            |

The renderer never touches a terminal. It produces the same bytes
`FlowerRenderer` writes in-process, and the wrapper writes them through the same
`OutputArbiter`, so a child’s graphics upload still holds them back.

## One tick

A tick is one synchronous request and reply, so ordering is the same as
in-process: the runtime is a pure function of the events and durations it is given.

```
wrapper → renderer   {"t": "tick", "d": 0.0167, "cell": [12, 4],
                      "vp": [30, 100, 1600, 990], "drawing": true,
                      "cells": [[3, 9]], "ev": [["move", 12, 4]]}
renderer → wrapper   {"wants": true, "say": []}  + 5 812 bytes of graphics
```

Between ticks the wrapper queues events rather than sending them: `move` on
a cursor report, `screen` with the reset sequence it saw, `wither` on focus
loss or caret handover. The renderer applies them in order before the frame.
`wants` is `wants_tick`; a queued event also counts, so an unfocused
window with a withering to show still ticks. `say` carries the renderer’s
messages for this window (a missing sound extra) back to the wrapper, which
holds them until the terminal is given back.

Every message is two big-endian `u32` lengths, then a JSON header, then an
opaque payload.

## Lifecycle

| Situation                                               | In-process                           | Shared renderer                                                                                                                                                                                                                                |
| ------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| First window opens                                      | loads everything, ~0.3 s             | spawns the renderer, waits for its socket, then says hello                                                                                                                                                                                     |
| Later window opens                                      | loads everything again               | connects to the running renderer; nothing to load                                                                                                                                                                                              |
| Two windows open at once                                | independent                          | the renderer holds an exclusive `flock`; the loser exits, both wrappers connect to the winner                                                                                                                                                  |
| Window closes                                           | clears its images, closes its device | asks the renderer to clear (bytes written by the wrapper), then disconnects                                                                                                                                                                    |
| Last window closes                                      | nothing left                         | renderer exits after 60 s with no connections                                                                                                                                                                                                  |
| Code, Python or artwork changes                         | new windows run new code and artwork | socket name carries a hash of the package source, the interpreter and every manifest’s identity (inode, mtime, size; each asset build replaces `manifest.json`), so new windows start a new renderer; the old one exits once its windows close |
| Socket deleted under a running renderer (a tmp cleaner) | n/a                                  | the renderer notices within a second and binds a new one                                                                                                                                                                                       |
| A window connects as the renderer exits idle            | n/a                                  | the connection fails, the wrapper spawns a new renderer, which waits up to 3 s for the old one’s lock                                                                                                                                          |
| Renderer crashes or hangs mid-session                   | n/a                                  | wrapper deletes its image-id range (`a=d,d=R`), then renders in-process; the window keeps its flower                                                                                                                                           |
| Renderer cannot start (5 s)                             | n/a                                  | wrapper renders in-process                                                                                                                                                                                                                     |
| Renderer refuses a window (bad manifest)                | fall back to the plain shell         | the same: the wrapper tries in-process, which fails the same way                                                                                                                                                                               |
| One window’s frame raises in the renderer               | that window’s traceback              | that connection is dropped; the other windows are untouched; that wrapper falls back to in-process                                                                                                                                             |

Socket: `$TMPDIR/flower-cursor-<uid>/<hash>.sock`, the directory created
`0700` and refused if anyone else owns it. `FLOWER_CURSOR_DAEMON=off` draws
in-process, and a directory there moves the socket. Neither has a flag. A
socket path longer than 103 bytes (macOS’s `sun_path` limit) draws in-process
at once rather than wait out the startup timeout.

## Audio

| Situation                 | In-process                             | Shared renderer                              |
| ------------------------- | -------------------------------------- | -------------------------------------------- |
| No bee audible anywhere   | no device                              | no device                                    |
| One window’s bee audible  | that window opens and starts a device  | the renderer opens its one device, starts it |
| Two windows’ bees audible | two devices                            | one device, the voices summed                |
| Every bee falls silent    | each device stops                      | the device stops                             |
| Device memory             | ~35 MB in each window that ever buzzed | ~35 MB once, in the renderer                 |

`BeeBuzz` is the same in both: in the renderer it is given a channel of a shared
`Mixer` as its device. The mixer’s audio callback reads an immutable tuple of
active voices and never takes a lock, so stopping the device cannot deadlock
against it.

## Cost, measured

With the poppy and three windows, a window drawing for itself is 41–45 MB
resident, 74–78 MB once its bee has buzzed. A wrapper is 28 MB whatever the
flower, and the renderer 38 MB, 75 MB with the audio device. N windows go from about 43·N MB to 28·N + 38 MB: three break even,
and each after that saves 15 MB, or 48 MB with the buzz on. CPU is the same
within the runs’ noise: the same frames are drawn, one process over, plus one
local socket round trip per tick.

## Not implemented

- `t=s` shared-memory transfer of raw RGBA. PNG encoding is about 5% of the
  renderer’s frame time (poppy, 1 200 focused frames with the cursor moving), against 34% choosing the pose
  and 21% LANCZOS resizing; and a frame Kitty never reads would leave its
  shared-memory object behind until reboot.
- A compiled wrapper, which would take the 28 MB towards the 10 MB `login`
  process Kitty would otherwise run.
