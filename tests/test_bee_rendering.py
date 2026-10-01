import base64
import io
import json
import math
import random
import re
from dataclasses import dataclass, replace

import pytest
from PIL import Image

from flower_cursor.bee import BeePose
from flower_cursor.bee_art import ASSETS, BeeCompositor
from flower_cursor.geometry import ViewportGeometry
from flower_cursor.graphics_renderer import DOWNSCALE, FlowerRenderer
from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.multi_runtime import MultiCursorFlowers
from flower_cursor.observer import DEFAULT_MANIFEST

A = CursorCell(8, 12)
VIEWPORT = ViewportGeometry(30, 100, 1600, 990)


def rgba_pixels(image):
  return [image.getpixel((x, y)) for y in range(image.height) for x in range(image.width)]


def commands(output):
  return [
    (dict(item.split("=", 1) for item in parameters.decode().split(",")), payload)
    for parameters, payload in re.findall(rb"\x1b_G([^;]*);(.*?)\x1b\\", output, re.DOTALL)
  ]


def transmission(output):
  parts = [(p, data) for p, data in commands(output) if p.get("a") == "T" or "a" not in p]
  image = Image.open(io.BytesIO(base64.b64decode(b"".join(data for _, data in parts))))
  image.load()
  parameters = parts[0][0]
  row, column = map(int, re.search(rb"\x1b\[(\d+);(\d+)H", output).groups())
  cw, ch = VIEWPORT.cell_size
  origin = (
    (column - 1) * cw + int(parameters.get("X", 0)),
    (row - 1) * ch + int(parameters.get("Y", 0)),
  )
  return parameters, image, origin


@dataclass(frozen=True)
class KittyPlacement:
  image_id: int
  placement_id: int
  z: int
  image: Image.Image
  origin: tuple[int, int]


class KittyScene:
  def __init__(self, viewport):
    self.viewport = viewport
    self.images = {}
    self.placements = {}
    self.cursor = (0, 0)
    self.saved_cursor = self.cursor
    self.pending = None

  def feed(self, output):
    tokens = re.finditer(rb"\x1b_G([^;]*);(.*?)\x1b\\|\x1b\[(\d+);(\d+)H|\x1b\[([su])", output, re.DOTALL)
    for token in tokens:
      control, payload, row, column, save_restore = token.groups()
      if row is not None:
        cw, ch = self.viewport.cell_size
        self.cursor = ((int(column) - 1) * cw, (int(row) - 1) * ch)
        continue
      if save_restore is not None:
        if save_restore == b"s":
          self.saved_cursor = self.cursor
        else:
          self.cursor = self.saved_cursor
        continue
      parameters = dict(item.split("=", 1) for item in control.decode().split(","))
      action = parameters.get("a")
      if action in ("T", "t"):
        assert self.pending is None
        self.pending = (parameters, bytearray(), self.cursor)
      if self.pending is not None:
        initial, encoded, cursor = self.pending
        encoded.extend(payload)
        if parameters.get("m", "0") == "1":
          continue
        image_id = int(initial["i"])
        image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
        self.images[image_id] = image
        self.placements = {key: value for key, value in self.placements.items() if key[0] != image_id}
        self.pending = None
        if initial["a"] == "T":
          self._place(initial, cursor)
      elif action == "p":
        self._place(parameters, self.cursor)
      elif action == "d":
        assert parameters["d"] in ("i", "I")
        image_id = int(parameters["i"])
        placement_id = int(parameters["p"]) if "p" in parameters else None
        self.placements = {
          key: value
          for key, value in self.placements.items()
          if key[0] != image_id or (placement_id is not None and key[1] != placement_id)
        }
        if parameters["d"] == "I" and not any(key[0] == image_id for key in self.placements):
          self.images.pop(image_id, None)
      else:
        pytest.fail(f"unexpected graphics command: {parameters}")
    assert self.pending is None

  def _place(self, parameters, cursor):
    assert "c" not in parameters and "r" not in parameters
    image_id, placement_id = int(parameters["i"]), int(parameters["p"])
    origin = tuple(cursor[i] + int(parameters.get(key, 0)) for i, key in enumerate(("X", "Y")))
    self.placements[image_id, placement_id] = KittyPlacement(
      image_id, placement_id, int(parameters.get("z", 0)), self.images[image_id], origin
    )

  def ordered(self):
    return sorted(self.placements.values(), key=lambda placement: (placement.z, placement.image_id))

  def frame(self, placements=None):
    canvas = Image.new("RGBA", (self.viewport.width, self.viewport.height))
    for placement in self.ordered() if placements is None else placements:
      canvas.alpha_composite(placement.image, placement.origin)
    return canvas


def advance(runtime, duration):
  for _ in range(round(duration * 60)):
    runtime.update(1 / 60)


@pytest.fixture
def scene():
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(31))
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  writes = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor, canvas_scale=2)
  runtime.set_geometry(VIEWPORT, renderer.layout)
  runtime.move_to(A)
  # The wait between visits is randomized; pin it so the seconds below are
  # the visit's rather than the wait's.
  runtime.bee.interval = runtime.bee.wait = 10.0
  advance(runtime, 10)
  visitor = next(v for v in runtime.visuals() if isinstance(v, BeeVisual))
  return runtime, compositor, renderer, writes, visitor


@pytest.mark.parametrize("cell", [A, CursorCell(0, 0), CursorCell(0, 50)])
@pytest.mark.parametrize("custom", [False, True])
def test_real_rest_tracks_rendered_head_with_top_crop_left_crop_and_custom_layout(cell, custom):
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(31))
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  options = {"columns": 7, "rows": 3, "anchor_columns": 2, "anchor_rows": 2} if custom else {}
  renderer = FlowerRenderer(runtime.pose_library, lambda _: None, compositor=compositor, canvas_scale=2, **options)
  runtime.set_geometry(VIEWPORT, renderer.layout)
  runtime.move_to(cell)
  runtime.bee.interval = runtime.bee.wait = 10.0
  advance(runtime, 15)
  width = ((7 if custom else 5) * 16 - 8) * 2
  height = ((3 if custom else 2) * 33 - 2) * 2
  expected_size = (round(width * 0.20 * 1.75), round(height * 0.20 * 1.75))
  assert runtime.bee.size == expected_size
  anchor = json.loads((ASSETS / "manifest.json").read_text())["contacts"]["0"]["anchor"]
  for _ in range(90):
    runtime.update(1 / 60)
    flower = runtime.visuals()[0]
    local = tuple(sum(compositor.records[a.name]["head"][i] * w for a, w in flower.samples) for i in range(2))
    anchors = (2, 2) if custom else (3, 1)
    left = (cell.column - anchors[0]) * 16
    left += round(anchors[0] * 16 - runtime.pose_library.root_anchor[0] * width)
    top = (cell.row - anchors[1]) * 33 + 2
    head = (left + local[0] * width, top + local[1] * height)
    assert runtime.bee.head == pytest.approx(head)
    feet = tuple(
      center + (coordinate - 0.5) * size
      for center, coordinate, size in zip((runtime.bee.x, runtime.bee.y), anchor, expected_size)
    )
    contact = tuple(h + offset for h, offset in zip(head, runtime.bee.landing_offset))
    assert feet == pytest.approx(contact, abs=0.5)
    assert runtime.bee.size == expected_size


def test_full_visit_crosses_viewport_at_constant_size_and_renderer_reclaims_id(scene):
  runtime, compositor, renderer, writes, visitor = scene
  initial = compositor.frame(visitor)
  assert (
    visitor.bee.x - initial.width / 2 > VIEWPORT.width
    or visitor.bee.x + initial.width / 2 < 0
    or visitor.bee.y - initial.height / 2 > VIEWPORT.height
    or visitor.bee.y + initial.height / 2 < 0
  )
  renderer.render((visitor,), A, viewport=VIEWPORT)
  assert writes == []
  seen_visible = seen_clipped = False
  last_id = None
  deleted = set()
  for _ in range(15 * 60):
    runtime.update(1 / 60)
    visitors = tuple(v for v in runtime.visuals() if isinstance(v, BeeVisual))
    writes.clear()
    renderer.render(visitors, A, viewport=VIEWPORT)
    output = b"".join(writes)
    deleted.update(p["i"] for p, _ in commands(output) if p.get("d") == "I")
    if any(p.get("a") == "T" for p, _ in commands(output)):
      parameters, image, origin = transmission(output)
      seen_visible = True
      last_id = parameters["i"]
      frame = compositor.frame(visitors[0])
      artwork = frame.getchannel("A").getbbox()
      seen_clipped |= image.size != frame.crop(artwork).size
      assert 0 <= origin[0] <= VIEWPORT.width - image.width
      assert 0 <= origin[1] <= VIEWPORT.height - image.height
    if visitors:
      assert visitors[0].bee.size == (50, 45)
    else:
      assert (
        runtime.bee.x + runtime.bee.radius < 0
        or runtime.bee.x - runtime.bee.radius > VIEWPORT.width
        or runtime.bee.y + runtime.bee.radius < 0
        or runtime.bee.y - runtime.bee.radius > VIEWPORT.height
      )
      break
  else:
    pytest.fail("visitor did not finish")
  assert seen_visible and seen_clipped and last_id in deleted
  assert not renderer._placements and not renderer._image_bases


@pytest.mark.parametrize("origin", [(-13, 120), (1590, 120), (200, -11), (200, 980), (-13, -11), (1590, 980)])
def test_renderer_crops_real_bee_pixels_at_every_viewport_edge(scene, origin):
  _, compositor, renderer, writes, visitor = scene
  pose = replace(visitor.bee, bank=18, folded=0.3, wing_phase=0.23)
  full = compositor.frame(replace(visitor, bee=pose))
  artwork = full.getchannel("A").getbbox()
  visitor = replace(
    visitor,
    bee=replace(
      pose,
      x=origin[0] - artwork[0] + full.width / 2,
      y=origin[1] - artwork[1] + full.height / 2,
    ),
  )
  full = full.crop(artwork)
  renderer.render((visitor,), A, viewport=VIEWPORT)
  parameters, image, placed = transmission(b"".join(writes))
  bounds = (
    max(0, -origin[0]),
    max(0, -origin[1]),
    min(full.width, VIEWPORT.width - origin[0]),
    min(full.height, VIEWPORT.height - origin[1]),
  )
  expected = full.crop(bounds)
  assert image.size == expected.size
  assert image.tobytes() == expected.tobytes()
  assert placed == (max(0, origin[0]), max(0, origin[1]))
  assert parameters["p"] == str(visitor.identifier)
  assert "c" not in parameters and "r" not in parameters
  assert parameters["C"] == "1"


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom", "missing"])
def test_offscreen_or_missing_viewport_frees_owned_bee_image(scene, edge):
  _, compositor, renderer, writes, visitor = scene
  visitor = replace(visitor, bee=replace(visitor.bee, x=250, y=150))
  renderer.render((visitor,), A, viewport=VIEWPORT)
  previous = transmission(b"".join(writes))[0]
  frame = compositor.frame(visitor)
  centers = {
    "left": (-frame.width / 2, 150),
    "right": (VIEWPORT.width + frame.width / 2, 150),
    "top": (250, -frame.height / 2),
    "bottom": (250, VIEWPORT.height + frame.height / 2),
    "missing": (250, 150),
  }
  x, y = centers[edge]
  writes.clear()
  renderer.render(
    (replace(visitor, bee=replace(visitor.bee, x=x, y=y)),),
    A,
    viewport=None if edge == "missing" else VIEWPORT,
  )
  operations = [p for p, _ in commands(b"".join(writes))]
  assert operations == [{"a": "d", "d": "I", "i": previous["i"], "p": str(visitor.identifier), "q": "2"}]
  assert visitor.identifier not in renderer._image_bases
  writes.clear()
  renderer.clear()
  assert writes == []


def test_identical_pixels_moving_within_one_cell_update_offsets_and_cache_exact_placement(scene):
  _, _, renderer, writes, visitor = scene
  visitor = replace(visitor, bee=replace(visitor.bee, x=250, y=150))
  renderer.render((visitor,), A, viewport=VIEWPORT)
  first, pixels, origin = transmission(b"".join(writes))
  writes.clear()
  renderer.render((visitor,), A, viewport=VIEWPORT)
  assert writes == []
  moved = replace(visitor, bee=replace(visitor.bee, x=visitor.bee.x + 1, y=visitor.bee.y + 1))
  renderer.render((moved,), A, viewport=VIEWPORT)
  second, moved_pixels, moved_origin = transmission(b"".join(writes))
  assert moved.cell == visitor.cell
  assert moved_origin == (origin[0] + 1, origin[1] + 1)
  assert pixels.tobytes() == moved_pixels.tobytes()
  assert first["i"] != second["i"]
  assert any(p.get("a") == "d" and p["i"] == first["i"] for p, _ in commands(b"".join(writes)))
  writes.clear()
  renderer.render((moved,), A, viewport=VIEWPORT)
  assert writes == []
  renderer.render((visitor,), A, viewport=VIEWPORT)
  assert transmission(b"".join(writes))[0]["i"] == first["i"]


def test_viewport_resize_reclips_even_when_cell_size_does_not_change(scene):
  _, compositor, renderer, writes, visitor = scene
  visitor = replace(visitor, bee=replace(visitor.bee, x=790, y=500))
  renderer.render((visitor,), A, viewport=VIEWPORT)
  writes.clear()
  smaller = ViewportGeometry(16, 50, 800, 528)
  assert smaller.cell_size == VIEWPORT.cell_size
  renderer.render((visitor,), A, viewport=smaller)
  _, image, origin = transmission(b"".join(writes))
  full = compositor.frame(visitor)
  full = full.crop(full.getchannel("A").getbbox())
  assert image.width < full.width
  assert image.tobytes() == full.crop((0, 0, 800 - origin[0], full.height)).tobytes()
  assert origin[0] + image.width == 800


@pytest.mark.parametrize("origin", [(1280, 100), (100, 792), (1280, 792)])
def test_remainder_pixels_use_legal_cell_without_shifting_artwork(scene, origin):
  _, compositor, renderer, writes, visitor = scene
  viewport = ViewportGeometry(24, 80, 1283, 803)
  full = compositor.frame(visitor)
  artwork = full.getchannel("A").getbbox()
  left, top = origin[0] - artwork[0], origin[1] - artwork[1]
  visitor = replace(visitor, bee=replace(visitor.bee, x=left + full.width / 2, y=top + full.height / 2))
  renderer.render((visitor,), A, viewport=viewport)
  output = b"".join(writes)
  _, image, placed = transmission(output)
  row, column = map(int, re.search(rb"\x1b\[(\d+);(\d+)H", output).groups())
  assert 1 <= row <= viewport.rows
  assert 1 <= column <= viewport.columns
  assert placed[0] + image.width <= viewport.width
  assert placed[1] + image.height <= viewport.height
  expected = Image.new("RGBA", (viewport.width, viewport.height))
  expected.alpha_composite(full, (left, top))
  actual = Image.new("RGBA", expected.size)
  actual.alpha_composite(image, placed)
  assert actual.tobytes() == expected.tobytes()
  if origin != (1280, 792):
    assert actual.getchannel("A").getbbox()


@pytest.mark.parametrize("reset", [b"\x1b[2J", b"\x1b[?2J", b"\x1bc"])
def test_screen_reset_reclaims_bee_image_before_repaint(scene, reset):
  _, _, renderer, writes, visitor = scene
  visitor = replace(visitor, bee=replace(visitor.bee, x=250, y=150))
  renderer.render((visitor,), A, viewport=VIEWPORT)
  old = transmission(b"".join(writes))[0]
  writes.clear()
  assert renderer.observe_terminal_output(reset)
  renderer.render((visitor,), A, viewport=VIEWPORT)
  operations = [p for p, _ in commands(b"".join(writes)) if "a" in p]
  assert [p["a"] for p in operations] == ["d", "T"]
  assert operations[0]["i"] == old["i"] and operations[0]["d"] == "I"


def test_alternate_buffer_restoration_reclaims_only_its_owned_bee(scene):
  _, _, renderer, writes, visitor = scene
  visitor = replace(visitor, bee=replace(visitor.bee, x=250, y=150))
  renderer.render((visitor,), A, viewport=VIEWPORT)
  main_id = transmission(b"".join(writes))[0]["i"]
  writes.clear()
  assert renderer.observe_terminal_output(b"\x1b[?1049h")
  alternate = replace(visitor, identifier=visitor.identifier + 100)
  renderer.render((alternate,), A, viewport=VIEWPORT)
  alternate_id = transmission(b"".join(writes))[0]["i"]
  assert main_id != alternate_id
  writes.clear()
  renderer.clear()
  assert [p["i"] for p, _ in commands(b"".join(writes))] == [alternate_id]
  writes.clear()
  assert renderer.observe_terminal_output(b"\x1b[?1049l")
  renderer.render((), A, viewport=VIEWPORT)
  assert [p["i"] for p, _ in commands(b"".join(writes))] == [main_id]
  assert not renderer._image_bases


@pytest.mark.parametrize("cell", [A, CursorCell(0, 0), CursorCell(0, 50)])
@pytest.mark.parametrize("custom", [False, True])
def test_real_flower_pixels_and_placement_unchanged_by_shared_geometry(cell, custom):
  runtime = LayeredRuntime(DEFAULT_MANIFEST)
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  writes = []
  options = {"columns": 7, "rows": 3, "anchor_columns": 2, "anchor_rows": 2} if custom else {}
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor, canvas_scale=2, **options)
  runtime.move_to(cell)
  advance(runtime, 1)
  visual = runtime.visuals()[0]
  width, height = (208, 194) if custom else (144, 128)
  anchors = (2, 2) if custom else (3, 1)
  left = (cell.column - anchors[0]) * 16
  left += round(anchors[0] * 16 - runtime.pose_library.root_anchor[0] * width)
  top = (cell.row - anchors[1]) * 33 + 2
  expected = compositor.frame(visual).convert("RGBa").resize((width, height), DOWNSCALE).convert("RGBA")
  if left < 0 or top < 0:
    expected = expected.crop((max(0, -left), max(0, -top), width, height))
  renderer.render((visual,), cell, viewport=VIEWPORT)
  _, image, origin = transmission(b"".join(writes))
  assert image.size == expected.size
  assert image.tobytes() == expected.tobytes()
  assert origin == (max(0, left), max(0, top))


def test_primary_only_bee_survives_independent_copies_transitions_with_unique_ids(scene):
  runtime, _, renderer, writes, _ = scene
  extras = MultiCursorFlowers(DEFAULT_MANIFEST, independent_limit=2)
  cells = (CursorCell(8, 30), CursorCell(8, 40))
  for _ in range(5 * 60):
    runtime.update(1 / 60)
    extras.update(cells, 1 / 60)
  stamp = runtime.active.flower.identifier
  primary_bee = next(v for v in runtime.visuals() if isinstance(v, BeeVisual))
  bee_id = None
  for step in (cells, (*cells, CursorCell(8, 50)), cells, (), (*cells, CursorCell(8, 50))):
    extras.update(step, 0)
    # A transition leaves the flowers it dropped withering on screen; this
    # test counts what is settled, so let them finish going first.
    extras.advance(1.5)
    for secondary in extras._runtimes:
      secondary.set_geometry(VIEWPORT, renderer.layout)
      advance(secondary, 15)
      assert not secondary.bee.visible
    visuals = extras.visuals(runtime.visuals(), stamp)
    visitors = [v for v in visuals if isinstance(v, BeeVisual)]
    assert len(visitors) == 1
    assert visitors[0].cell == A and visitors[0].bee == primary_bee.bee
    if bee_id is None:
      bee_id = visitors[0].identifier
    assert visitors[0].identifier == bee_id
    assert len({v.identifier for v in visuals}) == len(visuals)
    assert len(visuals) == len(step) + 2
    assert extras.degraded == (len(step) > 2)
    writes.clear()
    renderer.render(visuals, A, viewport=VIEWPORT)
    if extras.degraded:
      transfers = [p for p, _ in commands(b"".join(writes)) if p.get("a") == "t"]
      placements = [p for p, _ in commands(b"".join(writes)) if p.get("a") == "p"]
      assert len(transfers) == 1 and len(placements) == len(step)
      assert all(p["p"] != str(bee_id) for p in placements)
  extras.reset()
  runtime.reset()
  renderer.render(extras.visuals(runtime.visuals(), None), A, viewport=VIEWPORT)
  assert not renderer._placements and not renderer._image_bases and not renderer._shared


@pytest.mark.parametrize("secondary_count", [0, 1, 3])
@pytest.mark.parametrize("z_index", [1, 17])
def test_recycled_lower_bee_image_id_stays_above_real_flowers(secondary_count, z_index):
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(31))
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  writes = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor, canvas_scale=2, z_index=z_index)
  runtime.set_geometry(VIEWPORT, renderer.layout)
  extras = MultiCursorFlowers(DEFAULT_MANIFEST, independent_limit=2)
  cells = tuple(CursorCell(A.row, A.column + i + 1) for i in range(secondary_count))
  terminal = KittyScene(VIEWPORT)

  def render():
    visuals = extras.visuals(runtime.visuals(), runtime.active.flower.identifier)
    writes.clear()
    renderer.render(visuals, A, viewport=VIEWPORT)
    terminal.feed(b"".join(writes))
    return visuals

  runtime.move_to(CursorCell(A.row, A.column - 10))
  advance(runtime, 1)
  render()
  retired_id = terminal.ordered()[0].image_id
  runtime.move_to(A)
  runtime.bee.interval = runtime.bee.wait = 10.0
  extras.update(cells, 0)
  render()
  for _ in range(3 * 60):
    runtime.update(1 / 60)
    extras.update(cells, 1 / 60)
  render()
  assert all(p.image_id > retired_id + 1 for p in terminal.ordered())
  for _ in range(12 * 60):
    runtime.update(1 / 60)
    extras.update(cells, 1 / 60)
  visuals = render()
  visitor = next(v for v in visuals if isinstance(v, BeeVisual))
  bee = next(p for p in terminal.ordered() if p.placement_id == visitor.identifier)
  flowers = [p for p in terminal.ordered() if p.placement_id != visitor.identifier]
  assert len(flowers) == secondary_count + 1
  assert bee.image_id in (retired_id, retired_id + 1)
  assert all(bee.image_id < p.image_id for p in flowers)
  assert bee.z == z_index + 1
  assert all(p.z == z_index for p in flowers)
  assert terminal.ordered() == [*sorted(flowers, key=lambda p: p.image_id), bee]
  assert extras.degraded == (secondary_count > 2)

  correct = terminal.frame([*flowers, bee])
  hidden = terminal.frame([bee, *flowers])
  assert correct.tobytes() != hidden.tobytes(), "real bee must overlap the rendered bloom"
  assert terminal.frame().tobytes() == correct.tobytes()

  runtime.move_to(CursorCell(A.row, A.column + 2))
  advance(runtime, 0.15)
  extras.update(cells, 0.15)
  render()
  updated = next(p for p in terminal.ordered() if p.placement_id == visitor.identifier)
  assert updated.image_id != bee.image_id
  assert updated.z == z_index + 1
  assert terminal.ordered()[-1] == updated
  assert all(key[1] != visitor.identifier or key[0] == updated.image_id for key in terminal.placements)


@pytest.mark.parametrize("viewport", [VIEWPORT, ViewportGeometry(30, 100, 800, 480)])
# Row 1 is the highest a full bloom fits; on row 0 the bee lands above the viewport.
@pytest.mark.parametrize("cell", [A, CursorCell(1, 50)])
@pytest.mark.parametrize("seed", [0, 1, 3, 8, 12, 13, 31])
def test_exported_near_feet_contact_visible_bloom_at_actual_render_scale(viewport, cell, seed):
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(seed))
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  writes = []
  renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor, canvas_scale=2)
  runtime.set_geometry(viewport, renderer.layout)
  runtime.move_to(cell)
  runtime.bee.interval = runtime.bee.wait = 10.0
  advance(runtime, 15)
  contact = json.loads((ASSETS / "manifest.json").read_text())["contacts"]["0"]
  near_feet = [foot for foot in contact["feet"] if foot[1] > contact["anchor"][1]]
  assert len(near_feet) == 3
  terminal = KittyScene(viewport)
  for _ in range(5):
    advance(runtime, 0.3)
    visuals = runtime.visuals()
    visitor = next(v for v in visuals if isinstance(v, BeeVisual))
    assert visitor.bee.folded == 1 and visitor.bee.bank == 0
    assert (visitor.bee.heading + 180) % 360 - 180 == pytest.approx(0, abs=1e-6)
    writes.clear()
    renderer.render(visuals, cell, viewport=viewport)
    terminal.feed(b"".join(writes))
    bee = next(p for p in terminal.ordered() if p.placement_id == visitor.identifier)
    flowers = [p for p in terminal.ordered() if p.placement_id != visitor.identifier]
    flower = terminal.frame(flowers)
    insect = terminal.frame([bee])
    composite = terminal.frame()
    frame = compositor.frame(visitor)
    origin = (
      math.floor(visitor.bee.x - frame.width / 2 + 0.5),
      math.floor(visitor.bee.y - frame.height / 2 + 0.5),
    )
    supported = 0
    for foot in near_feet:
      x, y = (round(origin[i] + foot[i] * frame.size[i]) for i in range(2))
      region = (x - 1, y - 1, x + 2, y + 2)
      pink = any(
        alpha >= 64 and red > green and red > blue for red, green, blue, alpha in rgba_pixels(flower.crop(region))
      )
      leg = insect.crop(region).getchannel("A").getextrema()[1] >= 16
      supported += pink and leg
    assert supported >= 2, "near-side feet must touch the actual coral bloom"
    bounds = flower.getchannel("A").getbbox()
    assert bounds is not None
    flower_pixels = rgba_pixels(flower.crop(bounds))
    bee_pixels = rgba_pixels(insect.crop(bounds))
    composite_pixels = rgba_pixels(composite.crop(bounds))
    bloom = [
      i for i, (red, green, blue, alpha) in enumerate(flower_pixels) if alpha >= 128 and red > green and red > blue
    ]
    assert bloom
    visible_area = sum(1 - bee_pixels[i][3] / 255 for i in bloom)
    assert visible_area >= len(bloom) / 3, "the bee must not hide the bloom"
    exposed = [i for i in bloom if bee_pixels[i][3] == 0]
    assert exposed and all(composite_pixels[i] == flower_pixels[i] for i in exposed)
    assert any(bee_pixels[i][3] >= 32 for i in bloom), "bee and bloom must really overlap"


def test_bundled_bee_has_periodic_real_heading_bands_and_projected_contacts():
  manifest = json.loads((ASSETS / "manifest.json").read_text())
  assert set(manifest["headings"]) == {-180, -135, -90, -45, -25, 0, 25, 45, 90, 135}
  suffixes = [f"_{h:+03d}" if h else "" for h in manifest["headings"]]
  expected = {f"bee_{index:02d}{suffix}.png" for index in range(16) for suffix in suffixes}
  assert set(manifest["frames"]) == expected
  assert {path.name for path in ASSETS.glob("*.png")} == expected
  pixels = {}
  for name in sorted(expected):
    with Image.open(ASSETS / name) as image:
      assert image.mode == "RGBA" and image.size == (manifest["size"],) * 2
      alpha = image.getchannel("A")
      bounds = alpha.getbbox()
      assert bounds is not None and alpha.getextrema() == (0, 255)
      assert bounds[0] > 0 and bounds[1] > 0
      assert bounds[2] < image.width and bounds[3] < image.height
      pixels[name] = image.tobytes()
  for index in range(16):
    assert len({pixels[f"bee_{index:02d}{suffix}.png"] for suffix in suffixes}) == len(suffixes)
  for heading in manifest["headings"]:
    contact = manifest["contacts"][str(heading)]
    assert len(contact["feet"]) == 6
    assert all(len(foot) == 2 and all(0 < value < 1 for value in foot) for foot in contact["feet"])
    assert contact["anchor"] == pytest.approx(tuple(sum(foot[i] for foot in contact["feet"]) / 6 for i in range(2)))


@pytest.mark.parametrize("folded, phase, index", [(0, 0, 0), (0, 0.375, 3), (0.375, 0.125, 10), (1, 0, 15)])
def test_heading_changes_actual_runtime_pixels_and_interpolates_authored_bands(folded, phase, index):
  compositor = BeeCompositor()
  pose = BeePose(250, 150, (50, 45), 0, folded, phase)
  rendered = {}
  for heading in (-180, -135, -90, -45, -25, 0, 25, 45, 90, 135):
    suffix = f"_{heading:+03d}" if heading else ""
    frame = compositor.frame(replace(pose, heading=heading))
    with Image.open(ASSETS / f"bee_{index:02d}{suffix}.png") as source:
      expected = source.convert("RGBa").resize(pose.size, Image.Resampling.LANCZOS).convert("RGBA")
    assert frame.size == pose.size
    assert frame.tobytes() == expected.tobytes()
    rendered[heading] = frame.tobytes()
  assert len(set(rendered.values())) == 10
  assert compositor.frame(pose).tobytes() == rendered[0]
  assert compositor.frame(replace(pose, heading=180)).tobytes() == rendered[-180]
  assert compositor.frame(replace(pose, heading=360)).tobytes() == rendered[0]
  for heading, bounds in ((-12.5, (-25, 0)), (12.5, (0, 25)), (157.5, (135, -180))):
    between = compositor.frame(replace(pose, heading=heading))
    assert between.size == pose.size
    assert between.tobytes() not in (rendered[bounds[0]], rendered[bounds[1]])
