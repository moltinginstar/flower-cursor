import json
import math
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from flower_cursor.layered import LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell, FlowerInstance

A = CursorCell(4, 8)
B = CursorCell(4, 20)


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
  image = Image.new("RGBA", (32, 32))
  for x in range(14, 18):
    for y in range(5, 10):
      image.putpixel((x, y), (240, 120, 140, 255))
  image.save(tmp_path / "tile.png")
  layers = ("complete", "body", "head", *(f"petal_{i}" for i in range(6)))
  record = {
    "name": "test",
    "file": "tile.png",
    "stem": 1,
    "bloom": 1,
    "vitality": 1,
    "maturity": 1,
    "head": [0.5, 0.25],
    "world_head": [0, 0, 2],
    "layers": {name: "tile.png" for name in layers},
    "centers": {name: [0, 0, 2] for name in layers},
    "depths": {name: 1 for name in layers},
  }
  path = tmp_path / "manifest.json"
  path.write_text(
    json.dumps(
      {
        "shedding_groups": [{"id": f"petal_{i}", "layer": f"petal_{i}", "order": i} for i in range(6)],
        "poses": [record],
        "root_anchor": [0.5, 0.44],
        "projection_right": [0.15, 0, 0],
        "projection_down": [0, 0, -0.15],
      }
    )
  )
  return path


def test_screen_reset_drops_flowers_without_reusing_identifiers(manifest):
  runtime = LayeredRuntime(manifest)
  runtime.move_to(A)
  runtime.update(0.3)
  runtime.move_to(B)
  runtime.update(0.1)
  old_ids = {v.identifier for v in runtime.visuals()}
  runtime.reset()
  assert runtime.visuals() == ()
  runtime.move_to(B)
  assert len(runtime.visuals()) == 1
  assert runtime.visuals()[0].identifier > max(old_ids)


def test_runtime_uses_idle_ring_without_snapping_when_departure_starts(manifest):
  data = json.loads(manifest.read_text())
  base = data["poses"][0]
  data["poses"] = [
    dict(base, name="idle_00", idle_phase=0),
    dict(base, name="idle_12", idle_phase=math.pi),
    dict(base, name="exit_full_00", idle_phase=5.1),
  ]
  manifest.write_text(json.dumps(data))
  runtime = LayeredRuntime(manifest)
  runtime.move_to(A)
  runtime.update(3)
  samples = runtime.visuals()[0].samples
  assert all(asset.name.startswith("idle_") for asset, _ in samples)
  runtime.move_to(B)
  assert runtime.visuals()[0].samples == samples


def test_live_return_keeps_identity_pose_velocity_and_forward_flights(manifest):
  runtime = LayeredRuntime(manifest)
  runtime.move_to(A)
  runtime.update(1.5)
  original = runtime.active
  runtime.move_to(B)
  runtime.update(0.15)
  pose = original.pose
  velocity = original._lean.velocity
  flights = {f.identifier: f.age for f in original.flights}
  runtime.move_to(A)
  assert runtime.active is original
  assert original.pose == pose
  assert original._lean.velocity == velocity
  runtime.update(0.025)
  assert {f.identifier: f.age for f in original.flights} == pytest.approx(
    {identifier: age + 0.05 for identifier, age in flights.items()}
  )


def test_dead_stalk_keeps_debris_but_return_creates_a_new_bud(manifest):
  runtime = LayeredRuntime(manifest)
  runtime.move_to(A)
  runtime.update(1.5)
  original = runtime.active
  runtime.move_to(B)
  runtime.update(0.55)
  assert original.flower.dead and original.flights
  assert original in runtime.flowers
  runtime.move_to(A)
  assert runtime.active is not original
  assert runtime.active.flower.identifier != original.flower.identifier
  assert runtime.active.pose.stem == 0
  runtime.update(0.55)
  assert original not in runtime.flowers


def test_idle_uses_wall_time_while_growth_keeps_double_speed(manifest):
  runtime = LayeredRuntime(manifest)
  runtime.move_to(A)
  runtime.update(0.5)
  reference = FlowerInstance.bud(1, A)
  for _ in range(120):
    reference.update(1 / 120)
  assert runtime.active.pose.stem == pytest.approx(reference.pose.stem)
  assert runtime.active.pose.idle_phase == pytest.approx(0.5 * 1.7)


def test_empty_blend_is_rejected(manifest):
  with pytest.raises(ValueError, match="empty pose sample"):
    LayeredCompositor(manifest)._blend((), "complete")


def test_flight_sprite_is_frozen_across_return_and_drawn_after_stalk_death(manifest):
  runtime = LayeredRuntime(manifest)
  compositor = LayeredCompositor(manifest)
  runtime.move_to(A)
  runtime.update(1.5)
  original = runtime.active
  runtime.move_to(B)
  runtime.update(0.15)
  compositor.frame(runtime.visuals()[0])
  key = next(iter(compositor._flights))
  captured = compositor._flights[key]
  runtime.move_to(A)
  runtime.update(0.05)
  compositor.frame(runtime.visuals()[0])
  assert compositor._flights[key] is captured
  runtime.move_to(B)
  runtime.update(0.55)
  assert original.flower.dead
  frame = compositor.frame(next(v for v in runtime.visuals() if v.flower is original))
  assert frame.getchannel("A").getbbox() is not None
  compositor.retain(set())
  assert not compositor._flights


@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("amount", [0.17, 0.5, 0.95, 1.4])
def test_bounded_scaling_matches_full_canvas_affine(manifest, amount, cropped):
  compositor = LayeredCompositor(_cropped(manifest) if cropped else manifest, size=32)
  with Image.open(manifest.parent / "tile.png") as tile:
    image = tile.convert("RGBa")
  anchor = (0.48, 0.22)
  x, y = (v * compositor.size for v in anchor)
  inverse = 1 / amount
  reference = image.transform(
    image.size,
    Image.Transform.AFFINE,
    (inverse, 0, x * (1 - inverse), 0, inverse, y * (1 - inverse)),
    Image.Resampling.BICUBIC,
  )
  layer = compositor._blend(_one(compositor), "head")
  actual = compositor._whole(compositor._scale(layer, anchor, amount))
  difference = ImageChops.difference(actual.convert("RGBA"), reference.convert("RGBA"))
  assert max(high for low, high in difference.getextrema()) <= 1


def _cropped(manifest: Path, box=(14, 5, 18, 10)) -> Path:
  # A copy beside the original rather than an edit to it: manifests are
  # parsed once per path and cached.
  directory = manifest.parent / "cropped"
  directory.mkdir()
  shutil.copy(manifest.parent / "tile.png", directory)
  data = json.loads(manifest.read_text())
  data["crops"] = {
    "canvas": [32, 32],
    "layers": {name: list(box) for name in data["poses"][0]["layers"]},
  }
  (directory / "manifest.json").write_text(json.dumps(data))
  return directory / "manifest.json"


def _one(compositor: LayeredCompositor):
  return ((compositor.library.assets[0], 1.0),)


def test_crops_keep_only_the_drawn_rectangle_and_draw_the_same_frames(manifest):
  frames = []
  for crop in (False, True):
    path = _cropped(manifest) if crop else manifest
    runtime = LayeredRuntime(path, bee=False)
    compositor = LayeredCompositor(path, size=32)
    drawn = []
    runtime.move_to(A)
    for step in range(150):
      if step == 90:
        runtime.move_to(B)
      runtime.update(1 / 60)
      drawn.extend(compositor.frame(v).tobytes() for v in runtime.visuals())
    frames.append(drawn)
    if crop:
      assert {image.size for image in compositor._images.values()} == {(8, 9)}
    else:
      assert compositor._crops == {}
  assert frames[0] == frames[1]


def test_crop_that_misses_content_is_refused(manifest):
  compositor = LayeredCompositor(_cropped(manifest, (14, 5, 18, 9)), size=32)
  with pytest.raises(ValueError, match="does not fit its 'complete' crop"):
    compositor._blend(_one(compositor), "complete")


def test_crops_apply_only_at_the_size_they_were_measured_on(manifest):
  assert LayeredCompositor(_cropped(manifest), size=48)._crops == {}


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_wall_time_does_not_mutate_live_state(manifest, duration):
  runtime = LayeredRuntime(manifest)
  runtime.move_to(A)
  before = runtime.active.pose
  with pytest.raises(ValueError):
    runtime.update(duration)
  assert runtime.active.pose == before
