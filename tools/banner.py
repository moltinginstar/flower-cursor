"""Render the README and social-preview banner: every species, the resting bee, and the repo slug.

The flowers are re-baked from the Blender scenes at a multiple of the runtime
canvas, because the shipped 256px layers would be upscaled several times over.
Each flower is one pose rather than the runtime's crossfade of two, which in a
still reads as ghosted petals. Proportions come from the runtime: each frame is
the size `FlowerLayout` gives it, and the bee rests where `IdleBee.landing`
puts it, at the size the layered runtime gives it, on the daisy's head.

    uv run python tools/banner.py             # bake what is missing, then compose
    uv run python tools/banner.py --rebake    # bake every flower and the bee again

Bakes are cached under assets/rendered/banner, so changing only the layout is
quick. Writes docs/banner@4x.png and docs/banner.png, the 1280x640 one to
upload as the social preview.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from flower_cursor.bee import REST_CONTACT
from flower_cursor.geometry import FlowerLayout

CACHE = REPO / "assets/rendered/banner"
SLUG = ("moltinginstar/", "flower-cursor")
WIDTH, HEIGHT = 1280, 640
BACKGROUND = (13, 15, 21)
GLOW = (27, 31, 44)
MUTED = (118, 124, 142)
CREAM = (244, 238, 226)
FONT = (Path.home() / "Library/Fonts/IosevkaSS14.ttc", "Iosevka Term SS14", "Bold Extended")
FONT_SIZE = 58
# Distance between neighboring roots, in frame widths: the frames overlap,
# since each canvas has room for petals to fall in.
SPACING = 0.29
# Between the lowest root and the top of the slug, in banner pixels.
GAP = 72
# The landed bee, wings folded, facing the viewer: what a rest settles into.
BEE_POSE = 15
# The layered runtime's bee is this fraction of the flower frame.
BEE_FRACTION = 0.20 * 1.75


@dataclass(frozen=True, slots=True)
class Flower:
  species: str
  pose: str
  script: str
  env: dict[str, str]

  @property
  def directory(self) -> Path:
    return CACHE / self.species


def flower(species: str, pose: str) -> Flower:
  directory = str(CACHE / species)
  if species == "anemone":
    return Flower(species, pose, "bake_layered_flower.py", {"FLOWER_LAYER_DIR": directory, "FLOWER_LAYER_POSES": pose})
  if species == "cornflower":
    return Flower(species, pose, "bake_cornflower.py", {"CORNFLOWER_OUTPUT": directory, "CORNFLOWER_POSES": pose})
  env = {"VARIETY_OUTPUT": str(CACHE), "VARIETY_SPECIES": species, "VARIETY_POSES": pose}
  return Flower(species, pose, "bake_variety.py", env)


# Left to right. Each pose is a moment of the idle sway that faces the
# viewer; the daisy, in the middle of the left half, is the one the bee is on.
FLOWERS = (
  flower("anemone", "idle_18"),
  flower("cornflower", "idle_03"),
  flower("daisy", "idle_11"),
  flower("crocus", "idle_22"),
  flower("poppy", "idle_08"),
  flower("dahlia", "idle_18"),
  flower("orchid", "idle_03"),
)
HOST = 2


def blender(script: str, env: dict[str, str]) -> None:
  executable = shutil.which("blender")
  if executable is None:
    sys.exit("banner: Blender is needed to bake the flowers and was not found on PATH")
  subprocess.run(
    [executable, "-b", "--python", str(REPO / "blender" / script)],
    env={**os.environ, **env},
    check=True,
    stdout=subprocess.DEVNULL,
  )


def bake(scale: int, rebake: bool) -> None:
  for item in FLOWERS:
    image = item.directory / f"{item.pose}_complete.png"
    if rebake and item.directory.exists():
      shutil.rmtree(item.directory)
    if not image.exists():
      print(f"baking {item.species} {item.pose}", flush=True)
      blender(item.script, {**item.env, "FLOWER_BAKE_SCALE": str(scale), "FLOWER_BAKE_LAYERS": "complete"})
  bee = CACHE / "bee"
  if rebake and bee.exists():
    shutil.rmtree(bee)
  if not (bee / f"bee_{BEE_POSE:02d}.png").exists():
    print("baking the bee", flush=True)
    env = {"BEE_OUTPUT": str(bee), "BEE_POSES": str(BEE_POSE), "BEE_HEADINGS": "0", "BEE_SIZE": str(72 * scale)}
    blender("bake_idle_bee.py", env)


def record(item: Flower) -> tuple[dict, tuple[float, float]]:
  data = json.loads((item.directory / "manifest.json").read_text())
  pose = next(p for p in data["poses"] if p["name"] == item.pose)
  return pose, tuple(data["root_anchor"])


def premultiplied(path: Path, size: tuple[int, int]) -> Image.Image:
  with Image.open(path) as image:
    return image.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def place(canvas: Image.Image, image: Image.Image, x: float, y: float) -> None:
  """Composite `image` with its top left at (x, y), which may lie off the canvas."""
  layer = Image.new("RGBA", canvas.size)
  layer.paste(image, (round(x), round(y)))
  canvas.alpha_composite(layer)


def row(frame: tuple[int, int], scale: float, size: tuple[int, int]) -> tuple[Image.Image, float]:
  """The flowers and the bee, roots on one line through the middle of `size`; also that line's height."""
  canvas = Image.new("RGBA", size)
  width, height = frame[0] * scale, frame[1] * scale
  drawn = (round(width), round(height))
  step = SPACING * width
  baseline = size[1] / 2
  for index, item in enumerate(FLOWERS):
    pose, root = record(item)
    left = size[0] / 2 + (index - (len(FLOWERS) - 1) / 2) * step - root[0] * width
    top = baseline - root[1] * height
    place(canvas, premultiplied(item.directory / pose["layers"]["complete"], drawn), left, top)
    if index == HOST:
      # IdleBee.landing with no offset: the bee's resting contact on the head.
      bee = (frame[0] * BEE_FRACTION * scale, frame[1] * BEE_FRACTION * scale)
      x = left + pose["head"][0] * width + (0.5 - REST_CONTACT[0]) * bee[0]
      y = top + pose["head"][1] * height + (0.5 - REST_CONTACT[1]) * bee[1]
      image = premultiplied(CACHE / "bee" / f"bee_{BEE_POSE:02d}.png", (round(bee[0]), round(bee[1])))
      place(canvas, image, x - bee[0] / 2, y - bee[1] / 2)
  return canvas, baseline


def font(size: int) -> ImageFont.FreeTypeFont:
  path, family, style = FONT
  for index in range(256):
    try:
      face = ImageFont.truetype(path, size, index=index)
    except OSError:
      break
    if face.getname() == (family, style):
      return face
  sys.exit(f"banner: {family} {style} is not in {path}")


def backdrop(scale: int) -> Image.Image:
  size = (WIDTH * scale, HEIGHT * scale)
  glow = Image.new("RGB", size, BACKGROUND)
  ImageDraw.Draw(glow).ellipse(
    (size[0] * 0.08, -size[1] * 0.35, size[0] * 0.92, size[1] * 1.1),
    fill=GLOW,
  )
  return glow.filter(ImageFilter.GaussianBlur(170 * scale)).convert("RGBA")


def compose(frame: tuple[int, int], scale: int) -> Image.Image:
  face = font(FONT_SIZE * scale)
  slug = "".join(SLUG)
  left, top, right, bottom = face.getbbox(slug)
  size = (WIDTH * scale, HEIGHT * scale)
  # The row's ink is not proportional to its frames, so measure it once at a
  # trial size and scale frames and spacing together to the slug's width.
  trial, _ = row(frame, scale, (size[0] * 2, size[1] * 2))
  ink = trial.getchannel("A").getbbox()
  fit = scale * (right - left) / (ink[2] - ink[0])
  flowers, baseline = row(frame, fit, size)
  ink = flowers.getchannel("A").getbbox()

  # Center the row and the slug as one block.
  block = (baseline - ink[1]) + GAP * scale + (bottom - top)
  shift = (size[1] - block) / 2 - ink[1]
  banner = backdrop(scale)
  place(banner, flowers, (size[0] - (right - left)) / 2 - ink[0], shift)
  text = (size[0] - (right - left)) / 2 - left
  draw = ImageDraw.Draw(banner)
  y = baseline + shift + GAP * scale - top
  draw.text((text, y), SLUG[0], font=face, fill=MUTED)
  draw.text((text + face.getlength(SLUG[0]), y), SLUG[1], font=face, fill=CREAM)
  return banner


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  parser.add_argument("--cell", default="48x105", help="Kitty cell in pixels, which sets the frame's shape")
  parser.add_argument("--bake-scale", type=int, default=16, help="render size over the bake's 512px canvas")
  parser.add_argument("--rebake", action="store_true", help="bake again even where a bake is cached")
  parser.add_argument("--out", type=Path, default=REPO / "docs", help="directory for banner.png and banner@4x.png")
  args = parser.parse_args()

  bake(args.bake_scale, args.rebake)
  cell = tuple(int(value) for value in args.cell.split("x"))
  # Only the root anchor differs between species, and the frame's size does not use it.
  frame = FlowerLayout((0.5, 1.0)).size(cell)
  master = compose(frame, 4)
  args.out.mkdir(parents=True, exist_ok=True)
  master.convert("RGB").save(args.out / "banner@4x.png", optimize=True)
  small = master.convert("RGBa").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).convert("RGB")
  small.save(args.out / "banner.png", optimize=True)
  print(args.out / "banner.png")


if __name__ == "__main__":
  main()
