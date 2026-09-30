import base64
import io
import re
from dataclasses import replace
from random import Random

import pytest
from PIL import Image

from flower_cursor.graphics_renderer import FlowerRenderer
from flower_cursor.layered import LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.pose_library import PoseLibrary
from flower_cursor.settings import DEFAULT_MANIFEST

A = CursorCell(4, 8)
B = CursorCell(4, 20)
# The layered artwork is authored at 256px and supersampled, so the renderer
# reserves two canvas pixels per terminal pixel: a 5x2 cell frame at 16x33 is
# 144x128, not the 72x64 the retired flat atlas drew.
NATIVE = (144, 128)
# The copies tests place one already-composed visual many times, so they need a
# library to lay out against and never sample from it.
_LIBRARY = PoseLibrary.from_manifest(DEFAULT_MANIFEST)


def grown() -> tuple[LayeredRuntime, LayeredCompositor]:
  """A part-grown flower at A, with the compositor that actually draws it.

  These tests assert on the bytes Kitty receives, so the pair has to be the
  live one rather than a stand-in; the bee is seeded so a frame never gains
  an extra placement between runs.
  """
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=Random(0))
  runtime.move_to(A)
  runtime.update(0.35)
  return runtime, LayeredCompositor(DEFAULT_MANIFEST)


def commands(output: bytes) -> list[tuple[dict[str, str], bytes]]:
  return [
    (dict(item.split("=", 1) for item in parameters.decode().split(",")), payload)
    for parameters, payload in re.findall(rb"\x1b_G([^;]*);(.*?)\x1b\\", output, re.S)
  ]


def transmitted_image(output: bytes) -> Image.Image:
  payload = b"".join(
    payload for parameters, payload in commands(output) if parameters.get("a") == "T" or "a" not in parameters
  )
  image = Image.open(io.BytesIO(base64.b64decode(payload)))
  image.load()
  return image


def test_renderer_uses_absolute_placement_and_native_pixels_at_the_root() -> None:
  runtime, compositor = grown()
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)

  renderer.render(runtime.visuals(), B, cell_size=(16, 33))

  output = b"".join(writes)
  parameters = commands(output)[0][0]
  image = transmitted_image(output)
  row, column = map(int, re.search(rb"\x1b\[(\d+);(\d+)H", output).groups())
  root_x = (column - 1) * 16 + int(parameters["X"])
  root_x += runtime.pose_library.root_anchor[0] * image.width
  assert abs(root_x - A.column * 16) <= 0.5
  assert row == A.row
  assert image.size == NATIVE
  assert image.mode == "RGBA"
  assert parameters["p"] == str(runtime.visuals()[0].identifier)
  assert parameters["C"] == "1"
  assert parameters["z"] == "1"
  assert "c" not in parameters and "r" not in parameters
  assert output.startswith(b"\x1b[s")
  assert output.endswith(b"\x1b[u")


def test_expanded_canvas_preserves_root_position_and_native_flower_scale() -> None:
  runtime, compositor = grown()
  old_root = runtime.pose_library.root_anchor
  expanded = PoseLibrary(runtime.pose_library.assets, root_anchor=(old_root[0] / 2 + 0.25, old_root[1] / 2))

  class ExpandedCompositor:
    def frame(self, visual, duration):
      return Image.new("RGBA", (256, 256))

    def retain(self, identifiers):
      pass

  writes = []
  renderer = FlowerRenderer(expanded, writes.append, compositor=ExpandedCompositor(), canvas_scale=2)
  renderer.render(runtime.visuals(), A, cell_size=(16, 33))
  output = b"".join(writes)
  parameters = commands(output)[0][0]
  image = transmitted_image(output)
  row, column = map(int, re.search(rb"\x1b\[(\d+);(\d+)H", output).groups())
  root_x = (column - 1) * 16 + int(parameters["X"]) + expanded.root_anchor[0] * image.width
  assert image.size == (144, 128)
  assert abs(root_x - A.column * 16) <= 0.5
  assert row == A.row
  assert expanded.root_anchor[1] * image.height == pytest.approx(old_root[1] * 64)


def test_renderer_skips_unchanged_pixels_and_alternates_two_image_slots() -> None:
  runtime, compositor = grown()
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render(runtime.visuals(), A)
  first_id = commands(b"".join(writes))[0][0]["i"]
  writes.clear()
  renderer.render(runtime.visuals(), A)
  assert writes == []

  image_ids = [first_id]
  for _ in range(2):
    runtime.update(0.15)
    writes.clear()
    renderer.render(runtime.visuals(), A)
    operations = [p for p, _ in commands(b"".join(writes)) if "a" in p]
    assert [p["a"] for p in operations] == ["T", "d"]
    assert operations[1]["i"] == image_ids[-1]
    assert operations[1]["d"] == "I"
    image_ids.append(operations[0]["i"])
  assert image_ids[0] != image_ids[1]
  assert image_ids[0] == image_ids[2]


def test_each_flower_owns_its_image_even_when_the_pose_is_shared() -> None:
  runtime, compositor = grown()
  visual = runtime.visuals()[0]
  other = replace(visual, identifier=visual.identifier + 1, cell=B)
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)

  renderer.render((visual, other), B)

  transfers = [p for p, _ in commands(b"".join(writes)) if p.get("a") == "T"]
  assert len(transfers) == 2
  assert len({p["i"] for p in transfers}) == 2
  assert {int(p["p"]) for p in transfers} == {visual.identifier, other.identifier}


@pytest.mark.parametrize("clear", [False, True])
def test_renderer_frees_removed_images_and_cleanup_is_idempotent(clear: bool) -> None:
  runtime, compositor = grown()
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render(runtime.visuals(), A)
  image_id = commands(b"".join(writes))[0][0]["i"]
  writes.clear()

  if clear:
    renderer.clear()
  else:
    renderer.render((), A)

  operations = commands(b"".join(writes))
  assert len(operations) == 1
  assert operations[0][0] == {"a": "d", "d": "I", "i": image_id, "p": "1", "q": "2"}
  writes.clear()
  renderer.clear()
  assert writes == []


@pytest.mark.parametrize(
  "reset",
  [
    b"\x1b[2J",
    b"\x1b[3J",
    # ED 22 and the `?`-marked DECSED reach the same grman_clear.
    b"\x1b[22J",
    b"\x1b[?2J",
    b"\x1bc",
    b"\x1b[?47h",
    b"\x1b[?47l",
    b"\x1b[?1047h",
    b"\x1b[?1047l",
    b"\x1b[?1049h",
    b"\x1b[?1049l",
  ],
)
def test_screen_reset_split_across_chunks_reuploads_unchanged_frame(reset: bytes) -> None:
  runtime, compositor = grown()
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render(runtime.visuals(), A)
  writes.clear()

  renderer.observe_terminal_output(reset[:-1])
  renderer.render(runtime.visuals(), A)
  assert writes == []
  renderer.observe_terminal_output(reset[-1:])
  renderer.render(runtime.visuals(), A)
  assert any(p.get("a") == "T" for p, _ in commands(b"".join(writes)))


@pytest.mark.parametrize("mode", [b"47", b"1047", b"1049", b"25;1049"])
def test_restored_screen_cleans_its_own_retired_placements(mode: bytes) -> None:
  runtime, compositor = grown()
  visual = runtime.visuals()[0]
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render((visual,), A)
  main_id = commands(b"".join(writes))[0][0]["i"]
  writes.clear()

  assert renderer.observe_terminal_output(b"\x1b[?" + mode + b"h")
  assert writes == []
  renderer.render((replace(visual, identifier=99),), B)
  alternate_id = next(p["i"] for p, _ in commands(b"".join(writes)) if p.get("a") == "T")
  writes.clear()

  assert renderer.observe_terminal_output(b"\x1b[?" + mode + b"l")
  assert writes == []
  renderer.render((), A)
  assert commands(b"".join(writes)) == [
    ({"a": "d", "d": "I", "i": main_id, "p": str(visual.identifier), "q": "2"}, b"")
  ]
  writes.clear()
  renderer.observe_terminal_output(b"\x1b[?" + mode + b"h")
  renderer.render((), B)
  assert commands(b"".join(writes)) == [({"a": "d", "d": "I", "i": alternate_id, "p": "99", "q": "2"}, b"")]


def test_pending_cleanup_does_not_need_a_cursor_or_new_frame() -> None:
  runtime, compositor = grown()
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render(runtime.visuals(), A)
  writes.clear()
  assert not renderer.observe_terminal_output(b"\x1b[2")
  assert renderer.observe_terminal_output(b"J")
  assert writes == []
  renderer.flush_pending()
  assert len(commands(b"".join(writes))) == 1
  writes.clear()
  renderer.flush_pending()
  assert writes == []


def test_ordinary_output_does_not_invalidate_the_frame() -> None:
  runtime, compositor = grown()
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render(runtime.visuals(), A)
  writes.clear()
  renderer.observe_terminal_output(b"text\x1b[H\x1b[31m")
  renderer.render(runtime.visuals(), A)
  assert writes == []


def test_cell_resize_reuploads_native_pixel_geometry() -> None:
  runtime, compositor = grown()
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render(runtime.visuals(), A, cell_size=(8, 16))
  assert transmitted_image(b"".join(writes)).size == (66, 60)
  writes.clear()
  renderer.render(runtime.visuals(), A, cell_size=(16, 33))
  assert transmitted_image(b"".join(writes)).size == NATIVE


def test_left_edge_crops_image_without_shifting_the_root() -> None:
  cell = CursorCell(4, 0)
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=Random(0))
  runtime.move_to(cell)
  runtime.update(0.35)
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  writes: list[bytes] = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
  renderer.render(runtime.visuals(), cell, cell_size=(16, 33))

  output = b"".join(writes)
  image = transmitted_image(output)
  parameters = commands(output)[0][0]
  cropped = NATIVE[0] - image.width
  assert 0 < cropped < NATIVE[0]
  assert abs(runtime.pose_library.root_anchor[0] * NATIVE[0] - cropped) <= 0.5
  assert int(parameters["X"]) == 0
  assert b"\x1b[4;1H" in output


def test_two_renderers_in_one_window_do_not_share_image_ids() -> None:
  """Nested wrappers draw into the same id namespace, so bases must differ."""
  runtime, compositor = grown()

  bases = set()
  for _ in range(8):
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor)
    renderer.render(runtime.visuals(), A)
    bases.add(int(commands(b"".join(writes))[0][0]["i"]))

  assert len(bases) == 8
  # Low ids are what a program picks when it assumes it owns the window.
  assert min(bases) > 0xFFFF


def _copies(count: int):
  """One flower's visual stamped at many cells, as the copies path produces."""
  runtime, compositor = grown()
  source = runtime.visuals()[0]
  return tuple(
    replace(
      source,
      identifier=index + 1,
      cell=CursorCell(row=2 + index, column=10),
      appearance="one-flower",
    )
    for index in range(count)
  )


def test_copies_of_one_flower_are_transmitted_once_and_placed_many_times() -> None:
  written: list[bytes] = []
  renderer = FlowerRenderer(_LIBRARY, written.append, compositor=LayeredCompositor(DEFAULT_MANIFEST))
  renderer.render(_copies(40), CursorCell(row=2, column=10))
  output = b"".join(written)
  assert output.count(b"a=t,") == 1
  assert output.count(b"a=T,") == 0
  assert output.count(b"a=p,") == 40
  # The whole frame stays far below what 40 separate transmissions would cost.
  assert len(output) < 40_000, len(output)


def test_a_copied_frame_replaces_itself_without_erasing_its_other_copies() -> None:
  written: list[bytes] = []
  renderer = FlowerRenderer(_LIBRARY, written.append, compositor=LayeredCompositor(DEFAULT_MANIFEST))
  renderer.render(_copies(5), CursorCell(row=2, column=10))
  written.clear()
  moved = tuple(replace(visual, cell=CursorCell(row=visual.cell.row, column=11)) for visual in _copies(5))
  renderer.render(moved, CursorCell(row=2, column=11))
  output = b"".join(written)
  # Moving the copies re-places them; nothing frees the image they share.
  assert b"d=I" not in output
  assert output.count(b"a=p,") == 5


def test_dropping_the_copies_frees_the_image_they_shared() -> None:
  written: list[bytes] = []
  renderer = FlowerRenderer(_LIBRARY, written.append, compositor=LayeredCompositor(DEFAULT_MANIFEST))
  renderer.render(_copies(5), CursorCell(row=2, column=10))
  written.clear()
  renderer.render((), CursorCell(row=2, column=10))
  assert b"d=I" in b"".join(written)


def test_clearing_returns_the_shared_image_ids_for_reuse() -> None:
  """A cleared screen must not strand the ids its copies held.

  Bases are drawn from a bounded span, so leaking one per clear would
  eventually wrap the allocator onto ids still in use.
  """
  renderer = FlowerRenderer(_LIBRARY, lambda data: None, compositor=LayeredCompositor(DEFAULT_MANIFEST))
  for _ in range(3):
    renderer.render(_copies(5), CursorCell(row=2, column=10))
    renderer.clear()
  assert renderer._image_bases == {}
  assert renderer._allocated_bases == 1
