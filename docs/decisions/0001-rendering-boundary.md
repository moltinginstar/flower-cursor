# 0001: Make the flower the visible caret and keep position reporting native

- **Status:** accepted
- **Date:** 2026-09-16

## Decision

The flower renderer owns the visible caret while flower mode is active. The runtime hides Kitty's native cursor, uses terminal cursor-position reports for insertion-point accuracy, and renders the flower in the foreground through Kitty's graphics protocol. Placement stays compact and uses transparent pixels to limit obstruction of nearby terminal characters. The implemented representation is bundled, pre-rendered RGBA layers with premultiplied-alpha interpolation, independent petal flights, and phase-linear mature idle. The expanded transparent canvas preserves the original flower scale and root registration.

Lifecycle state is independent of Kitty frame transport. The runtime exposes continuous flower poses; the compositor and renderer turn them into images and placements. This keeps interruption and reversal testable without a live terminal.

## Evidence

Kitty's graphics protocol supports RGBA imagery, alpha blending, pixel offsets, placements larger than one cell, foreground z-order, and image/placement identifiers. The renderer uses these facilities for native-pixel, double-buffered flower updates.

The initial 2026-09-16 investigation targeted Kitty 0.48.2 and found custom shaders documented for 0.49.0. That historical version boundary motivated avoiding shaders; the current implementation still does not depend on them.

Custom kittens are Python programs with access to Kitty internals, but those APIs are explicitly documented as unstable and kittens normally run for a command invocation rather than acting as a permanent cursor observer. They remain an integration experiment, not an assumed architecture.

## Current observation boundary

The wrapper launches the shell/editor in a child PTY and polls standard cursor-position reports. Observation and rendering have independent target intervals. This needs no Kitty fork, but it only sees windows it wraps, and it gets no event for a cursor movement between polls.

## Open questions

A supported long-lived extension or a minimal upstream hook could replace the PTY wrapper. [0002](0002-integration-architecture.md) looks for one.

Injecting graphics into the child's output means never landing inside one of the child's escape sequences. A VT framer follows the stream and admits renderer writes only at sequence boundaries; child bytes are never buffered or truncated. Automated tests exercise real PTYs, startup output, child cursor replies during multipart uploads, graphics ordering, and cursor restoration. The renderer keeps per-buffer placement ownership and defers cleanup to safe write boundaries; the observer resets biological state across clears and screen switches and rejects cursor replies from the previous screen.

## Sources

- <https://sw.kovidgoyal.net/kitty/graphics-protocol/>
- <https://sw.kovidgoyal.net/kitty/kittens/custom/>
- <https://raw.githubusercontent.com/kovidgoyal/kitty/master/docs/custom-shaders.rst>
- <https://sw.kovidgoyal.net/kitty/multiple-cursors-protocol/>
