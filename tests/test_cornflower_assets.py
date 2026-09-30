import json
import math
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from flower_cursor.bee_art import ASSETS as BEE_ASSETS
from flower_cursor.geometry import FlowerLayout, ViewportGeometry
from flower_cursor.graphics_renderer import DOWNSCALE
from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import FLOWER_MANIFESTS

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = FLOWER_MANIFESTS["cornflower"]
A = CursorCell(4, 8)
B = CursorCell(4, 20)
VIEWPORT = ViewportGeometry(30, 100, 1600, 990)
SHEDDING_ORDER = (1, 7, 3, 9, 5, 11, 0, 6, 2, 8, 4, 10, 12, 15, 13, 16, 14, 17)
LAYERS = {"complete", "body", "head", *(f"petal_{index}" for index in range(18))}


def advance(runtime: LayeredRuntime, duration: float) -> None:
  while duration > 1e-10:
    step = min(1 / 60, duration)
    runtime.update(step)
    duration -= step


def test_real_cornflower_manifest_has_every_group_pose_and_png() -> None:
  data = json.loads(MANIFEST.read_text())
  assert data["species"] == "cornflower"
  assert [group["id"] for group in data["shedding_groups"]] == [f"petal_{index}" for index in range(18)]
  assert [group["layer"] for group in data["shedding_groups"]] == [f"petal_{index}" for index in range(18)]
  assert [group["order"] for group in data["shedding_groups"]] == list(range(18))
  assert tuple(data["shedding_order"]) == SHEDDING_ORDER
  assert data["landing_support_bounds"] == {"x": [-0.008, 0.008], "y": [-0.006, 0.006]}

  poses = data["poses"]
  assert len(poses) == 135
  assert len({pose["name"] for pose in poses}) == len(poses)
  files: set[str] = set()
  for pose in poses:
    assert set(pose["layers"]) == LAYERS
    assert pose["file"] in pose["layers"].values()
    for name in pose["layers"].values():
      path = Path(name)
      assert not path.is_absolute()
      assert ".." not in path.parts
      files.add(name)
      with Image.open(MANIFEST.parent / name) as image:
        assert image.mode == "RGBA"
        assert image.size == (256, 256)
        image.verify()

  assert len(files) == 135 * 21 == 2835
  assert files == {path.name for path in MANIFEST.parent.glob("*.png")}


def test_real_cornflower_head_projection_matches_world_head() -> None:
  data = json.loads(MANIFEST.read_text())
  root = data["root_anchor"]
  right = data["projection_right"]
  down = data["projection_down"]
  for pose in data["poses"]:
    world_head = pose["world_head"]
    assert len(world_head) == len(right) == len(down) == 3
    projected = (
      root[0] + sum(world * basis for world, basis in zip(world_head, right)),
      root[1] + sum(world * basis for world, basis in zip(world_head, down)),
    )
    assert pose["head"] == pytest.approx(projected, abs=1e-6)
    assert all(math.isfinite(value) for value in (*pose["head"], *world_head))


def test_real_cornflower_growth_idle_shedding_and_interrupted_return() -> None:
  runtime = LayeredRuntime(MANIFEST, species="cornflower")
  compositor = LayeredCompositor(MANIFEST, species="cornflower")
  runtime.move_to(A)

  runtime.update(0.1)
  growing = runtime.visuals()[0]
  assert 0 < runtime.active.pose.stem < 1
  assert compositor.frame(growing).getchannel("A").getbbox() is not None

  runtime.update(1.0)
  mature = runtime.visuals()[0]
  assert min(runtime.active.pose.stem, runtime.active.pose.bloom, runtime.active.pose.maturity) > 0.95
  assert any(asset.name.startswith("idle_") for asset, _ in mature.samples)
  idle_phase = runtime.active.pose.idle_phase
  runtime.update(0.25)
  assert runtime.active.pose.idle_phase != idle_phase
  assert compositor.frame(runtime.visuals()[0]).getchannel("A").getbbox() is not None

  returning = runtime.active
  runtime.move_to(B)
  runtime.update(0.3)
  assert returning.flights
  pose = returning.pose
  lean_velocity = returning._lean.velocity
  flight_state = [(flight.identifier, flight.age) for flight in returning.flights]

  runtime.move_to(A)
  assert runtime.active is returning
  assert returning.pose == pose
  assert returning._lean.velocity == lean_velocity
  runtime.update(0.02)
  assert [(flight.identifier, flight.age) for flight in returning.flights] == [
    (identifier, pytest.approx(age + 0.04)) for identifier, age in flight_state
  ]
  assert compositor.frame(runtime.visuals()[0]).getchannel("A").getbbox() is not None


def test_real_cornflower_all_eighteen_groups_detach_and_recover_continuously() -> None:
  runtime = LayeredRuntime(MANIFEST, species="cornflower")
  compositor = LayeredCompositor(MANIFEST, species="cornflower")
  runtime.move_to(A)
  runtime.update(2.0)
  departed = runtime.active
  runtime.move_to(B)
  runtime.update(0.55)

  assert [flight.index for flight in departed.flights] == list(SHEDDING_ORDER)
  assert all(petal.value == 0 for petal in departed.petals)
  assert departed in runtime.flowers
  assert (
    compositor.frame(next(v for v in runtime.visuals() if v.flower is departed)).getchannel("A").getbbox() is not None
  )

  old_identifier = departed.flower.identifier
  runtime.move_to(A)
  assert runtime.active is not departed
  assert runtime.active.flower.identifier > old_identifier
  runtime.update(0.5)
  assert compositor.frame(runtime.visuals()[0]).getchannel("A").getbbox() is not None

  runtime.update(1.5)
  assert departed not in runtime.flowers
  assert runtime.active is not None and not runtime.active.flower.dead
  assert compositor.frame(runtime.visuals()[0]).getchannel("A").getbbox() is not None


def test_real_cornflower_bee_uses_bounded_offset_and_touches_rendered_bloom() -> None:
  contact = json.loads((BEE_ASSETS / "manifest.json").read_text())["contacts"]["0"]
  near_feet = [foot for foot in contact["feet"] if foot[1] > contact["anchor"][1]]
  layout = FlowerLayout(LayeredRuntime(MANIFEST, species="cornflower").pose_library.root_anchor)
  for seed in (0, 9, 31):
    runtime = LayeredRuntime(MANIFEST, bee_rng=random.Random(seed), species="cornflower")
    compositor = LayeredCompositor(MANIFEST, species="cornflower")
    runtime.set_geometry(VIEWPORT, layout)
    runtime.move_to(CursorCell(8, 12))
    # The wait between visits is randomised; pin it so this budget covers
    # the visit rather than the wait.
    runtime.bee.interval = runtime.bee.wait = 10.0
    for _ in range(2000):
      runtime.update(1 / 60)
      if runtime.bee.visible and runtime.bee.folded == 1 and runtime.bee.departure is None:
        break
    else:
      pytest.fail("cornflower bee did not reach a resting visit")

    bounds = runtime.bee.landing_support_bounds
    assert bounds[0] == pytest.approx((-1.152, 1.152))
    assert bounds[1] == pytest.approx((-0.768, 0.768))
    assert bounds[0][0] <= runtime.bee.landing_offset[0] <= bounds[0][1]
    assert bounds[1][0] <= runtime.bee.landing_offset[1] <= bounds[1][1]

    flower_visual = runtime.visuals()[0]
    bee_visual = next(visual for visual in runtime.visuals() if isinstance(visual, BeeVisual))
    width, height = layout.size(VIEWPORT.cell_size)
    flower = compositor.frame(flower_visual).convert("RGBa").resize((width, height), DOWNSCALE).convert("RGBA")
    bee = compositor.frame(bee_visual)
    flower_origin = layout.origin(bee_visual.cell, VIEWPORT.cell_size)
    bee_origin = (
      math.floor(bee_visual.bee.x - bee.width / 2 + 0.5),
      math.floor(bee_visual.bee.y - bee.height / 2 + 0.5),
    )

    supported = 0
    for foot in near_feet:
      x = round(bee_origin[0] + foot[0] * bee.width)
      y = round(bee_origin[1] + foot[1] * bee.height)
      flower_pixels = []
      bee_pixels = []
      for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
          flower_x, flower_y = x - flower_origin[0] + dx, y - flower_origin[1] + dy
          if 0 <= flower_x < flower.width and 0 <= flower_y < flower.height:
            flower_pixels.append(flower.getpixel((flower_x, flower_y)))
          bee_x, bee_y = x - bee_origin[0] + dx, y - bee_origin[1] + dy
          if 0 <= bee_x < bee.width and 0 <= bee_y < bee.height:
            bee_pixels.append(bee.getpixel((bee_x, bee_y)))
      blue_bloom = any(alpha >= 64 and blue > red and blue > green for red, green, blue, alpha in flower_pixels)
      rendered_foot = any(alpha >= 16 for _, _, _, alpha in bee_pixels)
      supported += blue_bloom and rendered_foot

    assert supported >= 2


def test_installed_wheel_loads_and_renders_selected_cornflower_outside_checkout(tmp_path) -> None:
  uv = shutil.which("uv")
  assert uv is not None
  subprocess.run(
    [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
    cwd=ROOT,
    check=True,
    capture_output=True,
  )
  wheels = list(tmp_path.glob("*.whl"))
  assert len(wheels) == 1
  environment = os.environ.copy()
  environment.pop("PYTHONPATH", None)
  environment["FLOWER_CURSOR_CHECKOUT"] = str(ROOT)
  subprocess.run(
    [
      uv,
      "run",
      "--no-project",
      "--with",
      str(wheels[0]),
      "python",
      "-c",
      """
import os
import random
from pathlib import Path
from flower_cursor.geometry import FlowerLayout
from flower_cursor.layered import LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.settings import resolve
import flower_cursor

checkout = Path(os.environ["FLOWER_CURSOR_CHECKOUT"]).resolve()
assert not Path(flower_cursor.__file__).resolve().is_relative_to(checkout)
settings = resolve({"species": "cornflower"}, {})
manifest = settings.manifest
assert not manifest.resolve().is_relative_to(checkout)
runtime = LayeredRuntime(manifest, species=settings.species_name, bee_rng=random.Random(4))
compositor = LayeredCompositor(manifest, species=settings.species_name)
runtime.move_to(CursorCell(4, 8))
runtime.update(1.2)
visual = runtime.visuals()[0]
assert runtime.metadata.species_id == "cornflower"
assert len(runtime.metadata.shedding_groups) == 18
frame = compositor.frame(visual)
assert frame.size == (256, 256)
assert frame.getchannel("A").getbbox() is not None
""",
    ],
    cwd=tmp_path,
    env=environment,
    check=True,
    capture_output=True,
  )
