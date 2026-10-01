# 0001: Make the flower the visible caret and keep position reporting native

- **Status:** accepted
- **Date:** 2026-09-16

## Decision

The flower is the visible caret. The wrapper hides Kitty’s native cursor, finds the insertion point from cursor-position reports, and draws the flower in the foreground with Kitty’s graphics protocol. Transparent pixels keep it from covering nearby characters. The artwork is pre-rendered RGBA layers from Blender, blended with premultiplied alpha, so each petal can fall on its own and a grown flower sways through a loop of idle poses. The canvas has transparent padding, so the flower keeps its size and its stalk stays planted on the caret.

Lifecycle state is independent of Kitty frame transport. The runtime exposes continuous flower poses; the compositor and renderer turn them into images and placements. This keeps interruption and reversal testable without a live terminal.

## Evidence

Kitty’s graphics protocol supports RGBA imagery, alpha blending, pixel offsets, placements larger than one cell, foreground z-order, and image and placement IDs. The renderer uses them to draw at native resolution and swap frames between two image IDs.

Custom kittens can reach Kitty’s internals, but those APIs are documented as unstable, and a kitten runs for one invocation rather than staying around to watch the cursor.

## How the cursor is observed

The wrapper runs the shell or editor in a child PTY and polls standard cursor-position reports. Observation and rendering have independent target intervals. This needs no Kitty fork, but it only sees windows it wraps, and it gets no event for a cursor movement between polls.

[0002](0002-integration-architecture.md) looked for a supported extension point that could replace the PTY wrapper and found none.

## Sources

- <https://sw.kovidgoyal.net/kitty/graphics-protocol/>
- <https://sw.kovidgoyal.net/kitty/kittens/custom/>
- <https://sw.kovidgoyal.net/kitty/multiple-cursors-protocol/>
